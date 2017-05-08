# Copyright (C) 2014-2017 DLR
#
# All rights reserved. This program and the accompanying materials are made
# available under the terms of the Eclipse Public License v1.0 which
# accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
#
# Contributors:
# Annika Wollschlaeger <annika.wollschlaeger@dlr.de>
# Franz Steinmetz <franz.steinmetz@dlr.de>
# Lukas Becker <lukas.becker@dlr.de>
# Mahmoud Akl <mahmoud.akl@dlr.de>
# Michael Vilzmann <michael.vilzmann@dlr.de>
# Rico Belder <rico.belder@dlr.de>
# Sebastian Brunner <sebastian.brunner@dlr.de>

"""
.. module:: state
   :synopsis: A module to represent an abstract state in the state machine

"""

import Queue
import copy
import os
import sys
import threading
from __builtin__ import staticmethod
from weakref import ref

from enum import Enum
from gtkmvc import Observable
from jsonconversion.jsonobject import JSONObject
from yaml import YAMLObject

from rafcon.core.id_generator import *
from rafcon.core.state_elements.state_element import StateElement
from rafcon.core.state_elements.data_port import DataPort, InputDataPort, OutputDataPort
from rafcon.core.state_elements.outcome import Outcome
from rafcon.core.storage import storage
from rafcon.core.storage.storage import get_storage_id_for_state
from rafcon.utils import log
from rafcon.utils import multi_event
from rafcon.utils.constants import RAFCON_TEMP_PATH_STORAGE
from rafcon.utils.hashable import Hashable
from rafcon.core.decorators import lock_state_machine

logger = log.get_logger(__name__)
PATH_SEPARATOR = '/'


class State(Observable, YAMLObject, JSONObject, Hashable):

    """A class for representing a state in the state machine

    It inherits from Observable to make a change of its fields observable.

    :ivar str State.name: the name of the state
    :ivar str State.state_id: the id of the state
    :ivar dict State.input_data_ports: holds the input data ports of the state
    :ivar dict State.output_data_ports: holds the output data ports of the state
    :ivar dict State.outcomes: holds the state outcomes, which are the connection points for transitions
    :ivar rafcon.core.states.container_state.ContainerState State.parent: a reference to the parent state or None

    """

    _parent = None

    def __init__(self, name=None, state_id=None, input_data_ports=None, output_data_ports=None, outcomes=None,
                 parent=None):

        Observable.__init__(self)
        self._state_id = None
        self._name = None
        self._input_data_ports = {}
        self._output_data_ports = {}
        self._outcomes = {}
        # the input data of the state during execution
        self._input_data = {}
        # the output data of the state during execution
        self._output_data = {}
        # a flag which shows if the state was preempted from outside
        self._preempted = threading.Event()
        # a flag which shows if the state was started or resumed
        self._started = threading.Event()
        # a flag which shows if the state is paused
        self._paused = threading.Event()
        # a multi_event listening to both paused and preempted event
        self._interrupted = multi_event.create(self._preempted, self._paused)
        # a multi_event listening to both started and preempted event
        self._unpaused = multi_event.create(self._preempted, self._started)
        # a queue to signal a preemptive concurrency state, that the execution of the state finished
        self._concurrency_queue = None
        # the final outcome of a state, when it finished execution
        self._final_outcome = None
        self._description = None
        # detailed execution status of the state
        self._state_execution_status = None

        # before adding a state to a parent state or a sm the get_filesystem_path cannot return the file system path
        # therefore this variable caches the path until the state gets a parent
        self._file_system_path = None

        self.thread = None
        self._run_id = None

        if name is None:
            name = "Untitled"
        self.name = str(name) if isinstance(name, (int, float)) else name
        if state_id is None:
            self._state_id = state_id_generator()
        else:
            self._state_id = state_id

        self.parent = parent
        self.input_data_ports = input_data_ports if input_data_ports is not None else {}
        self.output_data_ports = output_data_ports if output_data_ports is not None else {}

        self.outcomes = outcomes if outcomes is not None else {0: Outcome(outcome_id=0, name="success")}
        self.state_execution_status = StateExecutionStatus.INACTIVE

        self.edited_since_last_execution = False
        self.execution_history = None
        self.backward_execution = False

        logger.debug("New {0} created".format(self))

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- generic methods -----------------------------------------
    # ---------------------------------------------------------------------------------------------

    def __del__(self):
        self._parent = None

    def __str__(self):
        return "{2} with name '{0}' and id '{1}'".format(self.name, self.state_id, type(self).__name__)

    def id(self):
        return self

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return str(self) == str(other)

    def __contains__(self, item):
        """Checks whether `item` is an element of the state

        Following child items are checked: outcomes, input data ports, output data ports

        :param item: State or state element
        :return: Whether item is a direct child of this state
        :rtype: bool
        """
        if not isinstance(item, StateElement):
            return False
        return item in self.outcomes.values() or item in self.input_data_ports.values() \
               or item in self.output_data_ports.values()

    @property
    def core_element_id(self):
        return self._state_id

    def to_dict(self):
        return self.state_to_dict(self)

    def update_hash(self, obj_hash):
        return Hashable.update_hash_from_dict(obj_hash, self.to_dict())

    @classmethod
    def from_dict(cls, dictionary):
        """ An abstract method each state has to implement.

        :param dictionary: the dictionary of parameters a state can be created from
        :raises exceptions.NotImplementedError: in any case
        """
        raise NotImplementedError()

    @staticmethod
    def state_to_dict(state):
        dict_representation = {
            'name': state.name,
            'state_id': state.state_id,
            'description': state.description,
            'input_data_ports': state.input_data_ports,
            'output_data_ports': state.output_data_ports,
            'outcomes': state.outcomes
        }
        return dict_representation

    @classmethod
    def to_yaml(cls, dumper, state):
        dict_representation = cls.state_to_dict(state)
        node = dumper.represent_mapping(cls.yaml_tag, dict_representation)
        return node

    @classmethod
    def from_yaml(cls, loader, node):
        dict_representation = loader.construct_mapping(node, deep=True)
        state = cls.from_dict(dict_representation)
        return state

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- execution functions -------------------------------------
    # ---------------------------------------------------------------------------------------------

    # give the state the appearance of a thread that can be started several times
    def start(self, execution_history, backward_execution=False):
        """ Starts the execution of the state in a new thread.

        :return:
        """
        self.execution_history = execution_history
        self._run_id = run_id_generator()
        self.backward_execution = copy.copy(backward_execution)
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def join(self):
        """ Waits until the state finished execution.

        """
        if self.thread:
            self.thread.join()
            self.thread = None
        else:
            logger.debug("Cannot join {0}, as the state hasn't been started, yet or is already finished!".format(self))

    def setup_run(self):
        """ Executes a generic set of actions that has to be called in the run methods of each derived state class.

        :raises exceptions.TypeError: if the input or output data are not of type dict
        """
        self.state_execution_status = StateExecutionStatus.ACTIVE
        self.preempted = False
        if not isinstance(self.input_data, dict):
            raise TypeError("input_data must be of type dict")
        if not isinstance(self.output_data, dict):
            raise TypeError("output_data must be of type dict")
        self.check_input_data_type()

    def setup_backward_run(self):
        self.state_execution_status = StateExecutionStatus.ACTIVE
        self.preempted = False

    def run(self, *args, **kwargs):
        """Implementation of the abstract run() method of the :class:`threading.Thread`

        :raises exceptions.NotImplementedError: in any case
        """
        raise NotImplementedError("The State.run() function has to be implemented!")

    def recursively_preempt_states(self):
        """Preempt the state
        """
        self.preempted = True
        self.paused = False
        self.started = False

    def recursively_pause_states(self):
        """Pause the state
        """
        self.started = False
        self.paused = True

    def recursively_resume_states(self):
        """Resume the state
        """
        self.started = True
        self.paused = False

    def get_previously_executed_state(self):
        """Calculates the state that was executed before this state

        :return: The last state in the execution history
        """
        return self.execution_history.get_last_history_item().prev.state_reference

    # ---------------------------------------------------------------------------------------------
    # ------------------------------- input/output data handling ----------------------------------
    # ---------------------------------------------------------------------------------------------

    def get_default_input_values_for_state(self, state):
        """ Computes the default input values for a state

        :param State state: the state to get the default input values for

        """
        from rafcon.core.states.library_state import LibraryState
        result_dict = {}
        for input_port_key, value in state.input_data_ports.iteritems():
            if isinstance(state, LibraryState):
                if state.use_runtime_value_input_data_ports[input_port_key]:
                    default = state.input_data_port_runtime_values[input_port_key]
                else:
                    default = value.default_value
            else:
                default = value.default_value
            # if the user sets the default value to a string starting with $, try to retrieve the value
            # from the global variable manager
            if isinstance(default, basestring) and len(default) > 0 and default[0] == '$':
                from rafcon.core.singleton import global_variable_manager as gvm
                var_name = default[1:]
                if not gvm.variable_exist(var_name):
                    logger.error("The global variable '{0}' does not exist".format(var_name))
                    global_value = None
                else:
                    global_value = gvm.get_variable(var_name)
                result_dict[value.name] = global_value
            else:
                # set input to its default value
                result_dict[value.name] = copy.copy(default)
        return result_dict

    @staticmethod
    def create_output_dictionary_for_state(state):
        """Return empty output dictionary for a state

        :param state: the state of which the output data is determined
        :return: the output data of the target state
        """
        from rafcon.core.states.library_state import LibraryState
        result_dict = {}
        for key, data_port in state.output_data_ports.iteritems():
            if isinstance(state, LibraryState) and state.use_runtime_value_output_data_ports[key]:
                result_dict[data_port.name] = copy.copy(state.output_data_port_runtime_values[key])
            else:
                result_dict[data_port.name] = copy.copy(data_port.default_value)
        return result_dict
    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- data port functions -------------------------------------
    # ---------------------------------------------------------------------------------------------

    @lock_state_machine
    @Observable.observed
    def add_input_data_port(self, name, data_type=None, default_value=None, data_port_id=None):
        """Add a new input data port to the state.

        :param str name: the name of the new input data port
        :param data_type: the type of the new output data port considered of class :class:`type` or
                          :class:`str` which has to be convertible to :class:`type`
        :param default_value: the default value of the data port
        :param int data_port_id: the data_port_id of the new data port
        :return: data_port_id of new input data port
        :rtype: int
        :raises exceptions.ValueError: if name of the input port is not unique
        """
        if data_port_id is None:
            # All data port ids have to passed to the id generation as the data port id has to be unique inside a state
            data_port_id = generate_data_port_id(self.get_data_port_ids())
        self._input_data_ports[data_port_id] = InputDataPort(name, data_type, default_value, data_port_id, self)

        # Check for name uniqueness
        valid, message = self._check_data_port_name(self._input_data_ports[data_port_id])
        if not valid:
            del self._input_data_ports[data_port_id]
            raise ValueError(message)

        return data_port_id

    @lock_state_machine
    @Observable.observed
    def remove_input_data_port(self, data_port_id, force=False):
        """Remove an input data port from the state

        :param int data_port_id: the id or the output data port to remove
        :param bool force: if the removal should be forced without checking constraints
        :raises exceptions.AttributeError: if the specified input data port does not exist
        """
        if data_port_id in self._input_data_ports:
            self.remove_data_flows_with_data_port_id(data_port_id)
            del self._input_data_ports[data_port_id]
        else:
            raise AttributeError("input data port with name %s does not exit", data_port_id)

    @lock_state_machine
    def remove_data_flows_with_data_port_id(self, data_port_id):
        """Remove all data flows whose from_key or to_key equals the passed data_port_id

        :param data_port_id: the id of a data_port of which all data_flows should be removed, the id can be a input or
                            output data port id

        """
        if not self.is_root_state:
            # delete all data flows in parent related to data_port_id and self.state_id
            data_flow_ids_to_remove = []
            for data_flow_id, data_flow in self.parent.data_flows.iteritems():
                if data_flow.from_state == self.state_id and data_flow.from_key == data_port_id or \
                        data_flow.to_state == self.state_id and data_flow.to_key == data_port_id:
                    data_flow_ids_to_remove.append(data_flow_id)

            for data_flow_id in data_flow_ids_to_remove:
                self.parent.remove_data_flow(data_flow_id)

    @lock_state_machine
    @Observable.observed
    def add_output_data_port(self, name, data_type, default_value=None, data_port_id=None):
        """Add a new output data port to the state

        :param str name: the name of the new output data port
        :param data_type: the type of the new output data port considered of class :class:`type` or
                          :class:`str` which has to be convertible to :class:`type`
        :param default_value: the default value of the data port
        :param int data_port_id: the data_port_id of the new data port
        :return: data_port_id of new output data port
        :rtype: int
        :raises exceptions.ValueError: if name of the output port is not unique
        """
        if data_port_id is None:
            # All data port ids have to passed to the id generation as the data port id has to be unique inside a state
            data_port_id = generate_data_port_id(self.get_data_port_ids())
        self._output_data_ports[data_port_id] = OutputDataPort(name, data_type, default_value, data_port_id, self)

        # Check for name uniqueness
        valid, message = self._check_data_port_name(self._output_data_ports[data_port_id])
        if not valid:
            del self._output_data_ports[data_port_id]
            raise ValueError(message)

        return data_port_id

    @lock_state_machine
    @Observable.observed
    def remove_output_data_port(self, data_port_id, force=False):
        """Remove an output data port from the state

        :param int data_port_id: the id of the output data port to remove
        :raises exceptions.AttributeError: if the specified input data port does not exist
        """
        if data_port_id in self._output_data_ports:
            self.remove_data_flows_with_data_port_id(data_port_id)
            del self._output_data_ports[data_port_id]
        else:
            raise AttributeError("output data port with name %s does not exit", data_port_id)

    def get_io_data_port_id_from_name_and_type(self, name, data_port_type):
        """Returns the data_port_id of a data_port with a certain name and data port type

        :param name: the name of the target data_port
        :param data_port_type: the data port type of the target data port
        :return: the data port specified by the name and the type
        :raises exceptions.AttributeError: if the specified data port does not exist in the input or output data ports
        """
        if data_port_type is InputDataPort:
            for ip_id, output_port in self.input_data_ports.iteritems():
                if output_port.name == name:
                    return ip_id
            raise AttributeError("Name '{0}' is not in input_data_ports".format(name))
        elif data_port_type is OutputDataPort:
            for op_id, output_port in self.output_data_ports.iteritems():
                if output_port.name == name:
                    return op_id
            # 'error' is an automatically generated output port in case of errors and exception and doesn't have an id
            if name == "error":
                return
            raise AttributeError("Name '{0}' is not in output_data_ports".format(name))

    def get_data_port_by_id(self, data_port_id):
        """Search for the given data port id in the data ports of the state

        The method tries to find a data port in the input and output data ports.
        :param int data_port_id: the unique id of the data port
        :return: the data port with the searched id or None if it is not found
        """
        if data_port_id in self.input_data_ports:
            return self.input_data_ports[data_port_id]
        elif data_port_id in self.output_data_ports:
            return self.output_data_ports[data_port_id]
        return None

    def get_data_port_ids(self):
        return self._input_data_ports.keys() + self._output_data_ports.keys()

    # ---------------------------------------------------------------------------------------------
    # ------------------------------------ outcome functions --------------------------------------
    # ---------------------------------------------------------------------------------------------

    def get_path(self, appendix=None, by_name=False):
        """ Recursively create the path of the state.

        The path is generated in bottom up method i.e. from the nested child states to the root state. The method
        concatenates either State.state_id (always unique) or State.name (maybe not unique but human readable) as
        state identifier for the path.

        :param str appendix: the part of the path that was already calculated by previous function calls
        :param bool by_name: The boolean enables name usage to generate the path
        :rtype: str
        :return: the full path to the root state
        """
        if by_name:
            state_identifier = self.name
        else:
            state_identifier = self.state_id

        if not self.is_root_state:
            if appendix is None:
                return self.parent.get_path(state_identifier, by_name)
            else:
                return self.parent.get_path(state_identifier + PATH_SEPARATOR + appendix, by_name)
        else:
            if appendix is None:
                return state_identifier
            else:
                return state_identifier + PATH_SEPARATOR + appendix

    def get_storage_path(self, appendix=None, old_delimiter=False):
        """ Recursively create the storage path of the state.

        The path is generated in bottom up method i.e. from the nested child states to the root state. The method
        concatenates the concatentation of (State.state_id and State.name) as state identifier for the path.

        :param str appendix: the part of the path that was already calculated by previous function calls
        :param bool old_delimiter: A flag to indicate the deprecated storage id
        :rtype: str
        :return: the full path to the root state
        """
        state_identifier = get_storage_id_for_state(self, old_delimiter)

        if not self.is_root_state:
            if appendix is None:
                return self.parent.get_storage_path(state_identifier, old_delimiter)
            else:
                return self.parent.get_storage_path(state_identifier + PATH_SEPARATOR + appendix, old_delimiter)
        else:
            if appendix is None:
                return state_identifier
            else:
                return state_identifier + PATH_SEPARATOR + appendix

    def get_state_machine(self):
        """Get a reference of the state_machine the state belongs to

        :rtype rafcon.core.state_machine.StateMachine
        :return: respective state machine
        """
        if self.parent:
            if self.is_root_state:
                return self.parent
            else:
                return self.parent.get_state_machine()

        return None

    @lock_state_machine
    def set_file_system_path(self, file_system_path):
        """Caches a temporary file system path for the state

        :param file_system_path:
        :return:
        """
        self._file_system_path = file_system_path

    def get_file_system_path(self):
        """Calculates the path in the filesystem where the state is stored

        :rtype: str
        :return: the path on the filesystem where the state is stored
        """
        if self.is_root_state_of_library:
            path = str(self.get_storage_path())
            # print "State_get_file_system_path -11: ", path, os.path.exists(path)
            if os.path.exists(path):
                return path
            else:
                path = str(self.get_storage_path(old_delimiter=True))
                # print "State_get_file_system_path -12: ", path
                return path
        elif not self.get_state_machine() or self.get_state_machine().file_system_path is None:
            if self._file_system_path:
                # print "State_get_file_system_path 0: "
                return self._file_system_path
            elif self.get_state_machine():
                if self.get_state_machine().supports_saving_state_names:
                    # print "State_get_file_system_path 11: ",
                    # os.path.join(RAFCON_TEMP_PATH_STORAGE, str(self.get_storage_path()))
                    path = os.path.join(RAFCON_TEMP_PATH_STORAGE, str(self.get_storage_path()))
                    if os.path.exists(path):
                        return path
                    else:
                        path = os.path.join(RAFCON_TEMP_PATH_STORAGE, str(self.get_storage_path(old_delimiter=True)))
                        return path
                else:
                    # print "State_get_file_system_path 12: "
                    return os.path.join(RAFCON_TEMP_PATH_STORAGE, str(self.get_path()))
            else:
                return os.path.join(RAFCON_TEMP_PATH_STORAGE, str(self.get_path()))
        else:
            if self.get_state_machine().supports_saving_state_names:
                path = os.path.join(self.get_state_machine().file_system_path, str(self.get_storage_path()))
                # print "State_get_file_system_path 21: ", path
                if os.path.exists(path):
                    return path
                else:
                    path = os.path.join(self.get_state_machine().file_system_path, str(self.get_storage_path(
                        old_delimiter=True)))
                    # print "State_get_file_system_path 22: ", path
                    return path
            else:
                # the default case for ID-formatted state machines when using a GUI
                # print "State_get_file_system_path 23: "
                return os.path.join(self.get_state_machine().file_system_path, self.get_path())

    @lock_state_machine
    @Observable.observed
    def add_outcome(self, name, outcome_id=None):
        """Add a new outcome to the state

        :param str name: the name of the outcome to add
        :param int outcome_id: the optional outcome_id of the new outcome

        :return: outcome_id: the outcome if of the generated state
        :rtype: int
        """
        if outcome_id is None:
            outcome_id = generate_outcome_id(self.outcomes.keys())
        if name in self._outcomes:
            logger.error("Two outcomes cannot have the same names")
            return
        if outcome_id in self.outcomes:
            logger.error("Two outcomes cannot have the same outcome_ids")
            return
        outcome = Outcome(outcome_id, name, self)
        self._outcomes[outcome_id] = outcome
        return outcome_id

    @lock_state_machine
    def remove(self, state_element, force=False):
        """Remove item from state

        :param StateElement state_element: State element to be removed
        :param bool force: if the removal should be forced without checking constraints
        """
        if isinstance(state_element, Outcome):
            self.remove_outcome(state_element.outcome_id, force)
        elif isinstance(state_element, InputDataPort):
            self.remove_input_data_port(state_element.data_port_id, force)
        elif isinstance(state_element, OutputDataPort):
            self.remove_output_data_port(state_element.data_port_id, force)
        else:
            raise ValueError("Cannot remove state_element with invalid type")

    @lock_state_machine
    @Observable.observed
    def remove_outcome(self, outcome_id, force=False):
        """Remove an outcome from the state

        :param int outcome_id: the id of the outcome to remove
        :raises exceptions.AttributeError: if the specified outcome does not exist or
                                            equals the aborted or preempted outcome
        """
        if outcome_id not in self.outcomes:
            raise AttributeError("There is no outcome_id %s" % str(outcome_id))

        if not force:
            if outcome_id == -1 or outcome_id == -2:
                raise AttributeError("You cannot remove the outcomes with id -1 or -2 as a state must always be able"
                                     "to return aborted or preempted")
        # Remove internal transitions to this outcome
        self.remove_outcome_hook(outcome_id)

        # delete possible transition connected to this outcome
        if not self.is_root_state:
            for transition_id, transition in self.parent.transitions.iteritems():
                if transition.from_outcome == outcome_id and transition.from_state == self.state_id:
                    self.parent.remove_transition(transition_id)
                    break  # found the one outgoing transition

        # delete outcome it self
        del self._outcomes[outcome_id]

    @lock_state_machine
    def remove_outcome_hook(self, outcome_id):
        """Hook for adding more logic when removing an outcome

        This hook is intended for the use of inherited classed, which can add more functionality if needed. A
        container state would remove its transitions going the removed outcome here.

        :param outcome_id: The id of the outcome that is removed
        """
        pass

    # ---------------------------------------------------------------------------------------------
    # -------------------------------------- check methods ---------------------------------------
    # ---------------------------------------------------------------------------------------------

    def check_child_validity(self, child):
        """Check validity of passed child object

        The method is called by state child objects (outcomes, data ports) when these are initialized or changed. The
        method checks the type of the child and then checks its validity in the context of the state.

        :param object child: The child of the state that is to be tested
        :return bool validity, str message: validity is True, when the child is valid, False else. message gives more
            information especially if the child is not valid
        """
        # Check type of child and call appropriate validity test
        if isinstance(child, Outcome):
            return self._check_outcome_validity(child)
        if isinstance(child, DataPort):
            return self._check_data_port_validity(child)
        return False, "no valid child type"

    def _check_outcome_validity(self, check_outcome):
        """Checks the validity of an outcome

        Checks whether the id or the name of the outcome is already used by another outcome within the state.

        :param rafcon.core.outcome.Outcome check_outcome: The outcome to be checked
        :return bool validity, str message: validity is True, when the outcome is valid, False else. message gives more
            information especially if the outcome is not valid
        """
        for outcome_id, outcome in self.outcomes.iteritems():
            # Do not compare outcome with itself when checking for existing name/id
            if check_outcome is not outcome:
                if check_outcome.outcome_id == outcome_id:
                    return False, "outcome id '{0}' existing in state".format(check_outcome.outcome_id)
                if check_outcome.name == outcome.name:
                    return False, "outcome name '{0}' existing in state".format(check_outcome.name)
        return True, "valid"

    def _check_data_port_validity(self, check_data_port):
        """Checks the validity of a data port

        Checks whether the data flows connected to the port do not conflict with the data types.

        :param rafcon.core.data_port.DataPort check_data_port: The data port to be checked
        :return bool validity, str message: validity is True, when the data port is valid, False else. message gives
            more information especially if the data port is not valid
        """
        valid, message = self._check_data_port_id(check_data_port)
        if not valid:
            return False, message

        valid, message = self._check_data_port_name(check_data_port)
        if not valid:
            return False, message

        # Check whether the type matches any connected data port type
        # Only relevant, if there is a parent state, otherwise the port cannot be connected to data flows
        # TODO: check of internal connections
        if not self.is_root_state:
            # Call the check in the parent state, where the data flows are stored
            return self.parent.check_data_port_connection(check_data_port)
        else:
            from rafcon.core.states.container_state import ContainerState
            if isinstance(self, ContainerState):
                return self.check_data_port_connection(check_data_port)

        return True, "valid"

    def _check_data_port_id(self, data_port):
        """Checks the validity of a data port id

        Checks whether the id of the given data port is already used by anther data port (input, output) within the
        state.

        :param rafcon.core.data_port.DataPort data_port: The data port to be checked
        :return bool validity, str message: validity is True, when the data port is valid, False else. message gives
            more information especially if the data port is not valid
        """
        for input_data_port_id, input_data_port in self.input_data_ports.iteritems():
            if data_port.data_port_id == input_data_port_id and data_port is not input_data_port:
                return False, "data port id already existing in state"
        for output_data_port_id, output_data_port in self.output_data_ports.iteritems():
            if data_port.data_port_id == output_data_port_id and data_port is not output_data_port:
                return False, "data port id already existing in state"
        return True, "valid"

    def _check_data_port_name(self, data_port):
        """Checks the validity of a data port name

        Checks whether the name of the given data port is already used by anther data port within the state. Names
        must be unique with input data ports and output data ports.

        :param rafcon.core.data_port.DataPort data_port: The data port to be checked
        :return bool validity, str message: validity is True, when the data port is valid, False else. message gives
            more information especially if the data port is not valid
        """
        if data_port.data_port_id in self.input_data_ports:
            for input_data_port in self.input_data_ports.itervalues():
                if data_port.name == input_data_port.name and data_port is not input_data_port:
                    return False, "data port name already existing in state's input data ports"

        elif data_port.data_port_id in self.output_data_ports:
            for output_data_port in self.output_data_ports.itervalues():
                if data_port.name == output_data_port.name and data_port is not output_data_port:
                    return False, "data port name already existing in state's output data ports"

        return True, "valid"

    def check_input_data_type(self):
        """Check the input data types of the state

        Checks all input data ports if the handed data is not of the specified type and generate an error logger message
        with details of the found type conflict.
        """
        for data_port in self.input_data_ports.itervalues():
            if data_port.name in self.input_data and self.input_data[data_port.name] is not None:
                #check for class
                if not isinstance(self.input_data[data_port.name], data_port.data_type):
                    logger.error("{0} had an data port error: Input of execute function must be of type {1} not {2} "
                                 "as current value {3}".format(self, data_port.data_type,
                                                               type(self.input_data[data_port.name]),
                                                               self.input_data[data_port.name]))

    def check_output_data_type(self):
        """Check the output data types of the state

        Checks all output data ports if the handed data is not of the specified type and generate an error logger
        message with details of the found type conflict.
        """
        for data_port in self.output_data_ports.itervalues():
            if data_port.name in self.output_data and self.output_data[data_port.name] is not None:
                # check for class
                if not isinstance(self.output_data[data_port.name], data_port.data_type):
                    logger.error("{0} had an data port error: Output of execute function must be of type {1} not {2} "
                                 "as current value {3}".format(self, data_port.data_type,
                                                               type(self.output_data[data_port.name]),
                                                               self.output_data[data_port.name]))

    # ---------------------------------------------------------------------------------------------
    # -------------------------------------- misc functions ---------------------------------------
    # ---------------------------------------------------------------------------------------------

    @lock_state_machine
    def change_state_id(self, state_id=None):
        """Changes the id of the state to a new id

        If no state_id is passed as parameter, a new state id is generated.

        :param str state_id: The new state id of the state
        :return:
        """
        if state_id is None:
            state_id = state_id_generator()
        if not self.is_root_state and not self.is_root_state_of_library:
            while state_id in self.parent.states:
                state_id = state_id_generator()

        self._state_id = state_id

    def get_states_statistics(self, hierarchy_level):
        """Get states statistic tuple

        :rtype: tuple
        :return: The number of child states. As per default states do not have child states return 1.
        """
        return 1, hierarchy_level + 1

    def get_number_of_transitions(self):
        """Generate the number of transitions

        :rtype: int
        :return: The number of transitions for a state. Per default states do not have transitions.
        """
        return 0

    @lock_state_machine
    def destruct(self):
        """ Removes all the state elements.

        :return:
        """
        for in_key in self.input_data_ports.keys():
            self.remove_input_data_port(in_key, True)

        for out_key in self.output_data_ports.keys():
            self.remove_output_data_port(out_key, True)

        for outcome_key in self.outcomes.keys():
            if outcome_key is not -1 and outcome_key is not -2:
                self.remove_outcome(outcome_key, True)

#########################################################################
# Properties for all class fields that must be observed by gtkmvc
#########################################################################

    @property
    def state_id(self):
        """Property for the _state_id field

        """
        return self._state_id

    @property
    def name(self):
        """Property for the _name field

        :rtype: str
        :return: Name of state
        """
        return self._name

    @name.setter
    @lock_state_machine
    @Observable.observed
    def name(self, name):
        if name is not None:
            if PATH_SEPARATOR in name:
                raise ValueError("Name must not include the \"" + PATH_SEPARATOR + "\" character")
            # if ID_NAME_DELIMITER in name:
            #     raise ValueError("Name must not include the \"" + ID_NAME_DELIMITER + "\" character")
            if not isinstance(name, basestring):
                raise TypeError("Name must be of type str")
            if len(name) < 1:
                raise ValueError("Name must have at least one character")

        if self._name and self._name != name and self.get_state_machine():
            # remove old path, as the state will be saved und another directory as its names changes
            storage.mark_path_for_removal_for_sm_id(self.get_state_machine().state_machine_id, self.get_file_system_path())
        self._name = name

    @property
    def parent(self):
        """Property for the _parent field

        :rtype: rafcon.core.states.container_state.ContainerState
        :return: A ContainState or value None if there is no parent or the parent is
                 :class:`rafcon.core.state_machine.StateMachine`
        """
        if not self._parent:
            return None
        return self._parent()

    @parent.setter
    @lock_state_machine
    @Observable.observed
    def parent(self, parent):
        if parent is None:
            self._parent = None
        else:
            from rafcon.core.state_machine import StateMachine
            if not isinstance(parent, (State, StateMachine)):
                raise TypeError("parent must be of type State or StateMachine")

            self._parent = ref(parent)

    @property
    def input_data_ports(self):
        """Property for the _input_data_ports field

        The setter-method substitute State._input_data_ports with a handed dictionary. The method checks if the
        elements in the dictionary are of the right type and the keys consistent (DataPort.data_port_id==key).
        The method does check validity of the elements by calling the parent-setter and in case of failure cancel
        the operation and recover old _input_data_ports.

        :return: Dictionary input_data_ports[:class:`int`, :class:`rafcon.core.state_elements.data_port.InputDataPort`]
                 that maps :class:`int` data_port_ids onto values of type InputDataPort
        :rtype: dict
        """
        return self._input_data_ports

    @input_data_ports.setter
    @lock_state_machine
    @Observable.observed
    def input_data_ports(self, input_data_ports):
        """Property for the _input_data_ports field

        See Property.

        :param dict input_data_ports: Dictionary that maps :class:`int` data_port_ids onto values of type
                                      :class:`rafcon.core.state_elements.data_port.InputDataPort`
        :raises exceptions.TypeError: if the input_data_ports parameter has the wrong type
        :raises exceptions.AttributeError: if the key of the input dictionary and the id of the data port do not match
        """
        if not isinstance(input_data_ports, dict):
            raise TypeError("input_data_ports must be of type dict")
        if [port_id for port_id, port in input_data_ports.iteritems() if not port_id == port.data_port_id]:
            raise AttributeError("The key of the input dictionary and the id of the data port do not match")

        # This is a fix for older state machines, which didn't distinguish between input and output ports
        for port_id, port in input_data_ports.iteritems():
            if not isinstance(port, InputDataPort):
                if isinstance(port, DataPort):
                    port = InputDataPort(port.name, port.data_type, port.default_value, port.data_port_id)
                    input_data_ports[port_id] = port
                else:
                    raise TypeError("Elements of input_data_ports must be of type InputDataPort, given: {0}".format(
                        type(port)))

        old_input_data_ports = self._input_data_ports
        self._input_data_ports = input_data_ports
        for port_id, port in input_data_ports.iteritems():
            try:
                port.parent = self
            except ValueError:
                self._input_data_ports = old_input_data_ports
                raise

    @property
    def output_data_ports(self):
        """Property for the _output_data_ports field

        The setter-method substitute State._output_data_ports with a handed dictionary. The method checks if the
        elements are of the right type and the keys consistent (DataPort.data_port_id==key). The method does check
        validity of the elements by calling the parent-setter and in case of failure cancel the operation and recover
        old _output_data_ports.

        :return: Dictionary output_data_ports[:class:`int`,
                 :class:`rafcon.core.state_elements.data_port.OutputDataPort`]
                 that maps :class:`int` data_port_ids onto values of type OutputDataPort
        :rtype: dict
        """
        return self._output_data_ports

    @output_data_ports.setter
    @lock_state_machine
    @Observable.observed
    def output_data_ports(self, output_data_ports):
        """ Setter for _output_data_ports field

        See property

        :param dict output_data_ports: Dictionary that maps :class:`int` data_port_ids onto values of type
                                      :class:`rafcon.core.state_elements.data_port.OutputDataPort`
        :raises exceptions.TypeError: if the output_data_ports parameter has the wrong type
        :raises exceptions.AttributeError: if the key of the output dictionary and the id of the data port do not match
        """
        if not isinstance(output_data_ports, dict):
            raise TypeError("output_data_ports must be of type dict")
        if [port_id for port_id, port in output_data_ports.iteritems() if not port_id == port.data_port_id]:
            raise AttributeError("The key of the output dictionary and the id of the data port do not match")

        # This is a fix for older state machines, which didn't distinguish between input and output ports
        for port_id, port in output_data_ports.iteritems():
            if not isinstance(port, OutputDataPort):
                if isinstance(port, DataPort):
                    port = OutputDataPort(port.name, port.data_type, port.default_value, port.data_port_id)
                    output_data_ports[port_id] = port
                else:
                    raise TypeError("Elements of output_data_ports must be of type OutputDataPort, given: {0}".format(
                        type(port)))

        old_output_data_ports = self._output_data_ports
        self._output_data_ports = output_data_ports
        for port_id, port in output_data_ports.iteritems():
            try:
                port.parent = self
            except ValueError:
                self._output_data_ports = old_output_data_ports
                raise

    @property
    def outcomes(self):
        """Property for the _outcomes field

        The setter-method substitute State._outcomes with a handed dictionary.
        The method checks if the elements are of the right type and the keys consistent (Outcome.outcome_id==key).
        The method does check validity of the elements by calling the parent-setter and in case of failure cancel
        the operation and recover old outcomes.

        :return: Dictionary outcomes[:class:`int`, :class:`rafcon.core.state_elements.outcome.Outcome`]
                 that maps :class:`int` outcome_ids onto values of type Outcome
        :rtype: dict
        """
        return self._outcomes

    @outcomes.setter
    @lock_state_machine
    @Observable.observed
    def outcomes(self, outcomes):
        """ Setter for _outcomes field

        See property.

        :param dict outcomes: Dictionary outcomes[outcome_id] that maps :class:`int` outcome_ids onto values of type
                              :class:`rafcon.core.state_elements.outcome.Outcome`
        :raises exceptions.TypeError: if outcomes parameter has the wrong type
        :raises exceptions.AttributeError: if the key of the outcome dictionary and the id of the outcome do not match
        """
        if not isinstance(outcomes, dict):
            raise TypeError("outcomes must be of type dict")
        if [outcome_id for outcome_id, outcome in outcomes.iteritems() if not isinstance(outcome, Outcome)]:
            raise TypeError("element of outcomes must be of type Outcome")
        if [outcome_id for outcome_id, outcome in outcomes.iteritems() if not outcome_id == outcome.outcome_id]:
            raise AttributeError("The key of the outcomes dictionary and the id of the outcome do not match")

        old_outcomes = self.outcomes
        self._outcomes = outcomes
        for outcome_id, outcome in outcomes.iteritems():
            try:
                outcome.parent = self
            except ValueError:
                self._outcomes = old_outcomes
                raise

        # aborted and preempted must always exist
        if -1 not in outcomes:
            self._outcomes[-1] = Outcome(outcome_id=-1, name="aborted", parent=self)
        if -2 not in outcomes:
            self._outcomes[-2] = Outcome(outcome_id=-2, name="preempted", parent=self)

    @property
    def input_data(self):
        """Property for the _input_data field

        """
        return self._input_data

    @input_data.setter
    @lock_state_machine
    #@Observable.observed
    def input_data(self, input_data):
        if not isinstance(input_data, dict):
            raise TypeError("input_data must be of type dict")
        self._input_data = input_data

    @property
    def output_data(self):
        """Property for the _output_data field

        """
        return self._output_data

    @output_data.setter
    @lock_state_machine
    #@Observable.observed
    def output_data(self, output_data):
        if not isinstance(output_data, dict):
            raise TypeError("output_data must be of type dict")
        self._output_data = output_data

    @property
    def preempted(self):
        """Checks, whether the preempted event is set
        """
        return self._preempted.is_set()

    @preempted.setter
    @lock_state_machine
    def preempted(self, preempted):
        if not isinstance(preempted, bool):
            raise TypeError("preempted must be of type bool")
        if preempted:
            self._preempted.set()
        else:
            self._preempted.clear()

    @property
    def started(self):
        """Checks, whether the started event is set
        """
        return self._started.is_set()

    @started.setter
    @lock_state_machine
    def started(self, started):
        if not isinstance(started, bool):
            raise TypeError("started must be of type bool")
        if started:
            self._started.set()
        else:
            self._started.clear()

    @property
    def paused(self):
        """Checks, whether the paused event is set
        """
        return self._paused.is_set()

    @paused.setter
    @lock_state_machine
    def paused(self, paused):
        if not isinstance(paused, bool):
            raise TypeError("paused must be of type bool")
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def wait_for_interruption(self, timeout=None):
        """Wait for any of the events paused or preempted to be set

        :param float timeout: Maximum time to wait, None if infinitely
        :return: True, is an event was set, False if the timeout was reached
        :rtype: bool
        """
        return self._interrupted.wait(timeout)

    def wait_for_unpause(self, timeout=None):
        """Wait for any of the events started or preempted to be set

        :param float timeout: Maximum time to wait, None if infinitely
        :return: True, is an event was set, False if the timeout was reached
        :rtype: bool
        """
        return self._unpaused.wait(timeout)

    @property
    def concurrency_queue(self):
        """Property for the _concurrency_queue field

        """
        return self._concurrency_queue

    @concurrency_queue.setter
    @lock_state_machine
    #@Observable.observed
    def concurrency_queue(self, concurrency_queue):
        if not isinstance(concurrency_queue, Queue.Queue):
            if not concurrency_queue is None:
                raise TypeError("concurrency_queue must be of type Queue or None")
            else:
                #concurrency_queue is None
                pass
        self._concurrency_queue = concurrency_queue

    @property
    def final_outcome(self):
        """Property for the _final_outcome field

        """
        return self._final_outcome

    @final_outcome.setter
    @lock_state_machine
    #@Observable.observed
    def final_outcome(self, final_outcome):
        if not isinstance(final_outcome, Outcome):
            raise TypeError("final_outcome must be of type Outcome")
        self._final_outcome = final_outcome

    @property
    def description(self):
        """Property for the _description field

        """
        return self._description

    @description.setter
    @lock_state_machine
    @Observable.observed
    def description(self, description):
        if not description:
            self._description = None
            return

        if not isinstance(description, (str, unicode)):
            if not isinstance(description, unicode):
                raise TypeError("Description must be of type str or unicode")

        self._description = description

    @property
    def active(self):
        """Property for the _active field

        """
        if self.state_execution_status is StateExecutionStatus.INACTIVE:
            return False
        else:
            return True

    @property
    def state_execution_status(self):
        """Property for the _state_execution_status field

        """
        return self._state_execution_status

    @state_execution_status.setter
    @lock_state_machine
    @Observable.observed
    def state_execution_status(self, state_execution_status):
        if not isinstance(state_execution_status, StateExecutionStatus):
            raise TypeError("state_execution_status must be of type StateExecutionStatus")

        self._state_execution_status = state_execution_status

    @property
    def is_root_state(self):
        return not isinstance(self.parent, State)

    @property
    def is_root_state_of_library(self):
        from rafcon.core.states.library_state import LibraryState
        return isinstance(self.parent, LibraryState)

    def finalize(self, outcome=None):
        """Finalize state

        This method is called when the run method finishes

        :param rafcon.core.outcome.Outcome outcome: final outcome of the state
        :return: Nothing for the moment
        """

        # Set the final outcome of the state
        if outcome is not None:
            self.final_outcome = outcome

        # If we are within a concurrency state, we have to notify it about our finalization
        if self.concurrency_queue:
            self.concurrency_queue.put(self.state_id)

        logger.debug("Finished execution of {0}: {1}".format(self, self.final_outcome))

        return None

    def preemptive_wait(self, time=None):
        """Waiting method which can be preempted

        Use this method if you want a state to pause. In contrast to time.sleep(), the pause can be preempted. This
        method can also be used if you want to have a daemon thread within a preemptive concurrency state. In this
        case, time has to be set to None and the method waits indefinitely or until it is preempted from outside.
        :param time: The time in seconds to wait or None (default) for infinity
        :return: True, if the wait was preempted, False else
        """
        return self._preempted.wait(time)

    @property
    def run_id(self):
        """Property for the _run_id field

        """
        return self._run_id


StateType = Enum('STATE_TYPE', 'EXECUTION HIERARCHY BARRIER_CONCURRENCY PREEMPTION_CONCURRENCY LIBRARY DECIDER_STATE')
StateExecutionStatus = Enum('STATE_EXECUTION_STATE', 'INACTIVE ACTIVE EXECUTE_CHILDREN WAIT_FOR_NEXT_STATE')