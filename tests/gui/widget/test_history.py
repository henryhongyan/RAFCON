from __future__ import print_function
from __future__ import absolute_import
from builtins import str
import os
import logging

# core elements
import rafcon.core.singleton
from rafcon.core.state_machine import StateMachine
from rafcon.core.states.state import State
from rafcon.core.states.execution_state import ExecutionState
from rafcon.core.states.container_state import ContainerState
from rafcon.core.states.hierarchy_state import HierarchyState
from rafcon.core.states.preemptive_concurrency_state import PreemptiveConcurrencyState
from rafcon.core.states.barrier_concurrency_state import BarrierConcurrencyState
from rafcon.core.storage import storage

# general tool elements
from rafcon.utils import log

logger = log.get_logger(__name__)
logger.setLevel(logging.VERBOSE)

# test environment elements
from tests import utils as testing_utils
from tests.utils import call_gui_callback
from tests.gui.widget.test_state_type_change import store_state_elements, check_state_elements, \
     check_list_ES, check_list_HS, check_list_BCS, check_list_PCS, \
     check_list_root_ES, check_list_root_HS, check_list_root_BCS, check_list_root_PCS, \
     get_state_editor_ctrl_and_store_id_dict, check_elements_ignores
from tests.gui.widget.test_states_editor import select_child_states_and_state_sequentially
import pytest

NO_SAVE = False
TEST_PATH = testing_utils.get_unique_temp_path()


def on_save_activate(state_machine_m, logger):
    if state_machine_m is None or NO_SAVE:
        return
    save_path = state_machine_m.state_machine.file_system_path
    if save_path is None:
        return

    logger.debug("Saving state machine to {0}".format(save_path))
    storage.save_state_machine_to_path(state_machine_m.state_machine,
                                      state_machine_m.state_machine.file_system_path, delete_old_state_machine=True)

    state_machine_m.root_state.store_meta_data()
    logger.debug("Successfully saved graphics meta data.")


def save_state_machine(sm_model, path, logger, with_gui=False, menubar_ctrl=None):

    def print_states(state):
        if isinstance(state, ContainerState):
            for state_id, child_state in state.states.items():
                print(child_state.get_path())
                print_states(child_state)
    print_states(sm_model.state_machine.root_state)

    print("do SAVING OF STATEMACHINE")
    if with_gui:
        call_gui_callback(sm_model.state_machine.__setattr__, "file_system_path", path)
        print("by Menubar_ctrl")
        call_gui_callback(menubar_ctrl.on_save_activate, None)
    else:
        sm_model.state_machine.file_system_path = path
        print("by Function")
        on_save_activate(sm_model, logger)

    from .test_storage import check_that_all_files_are_there

    if with_gui:
        call_gui_callback(check_that_all_files_are_there, sm_model.state_machine, path, False, True)
    else:
        check_that_all_files_are_there(sm_model.state_machine, path, False, True)


def create_state_machine():
    state_machine_path = testing_utils.get_test_sm_path(os.path.join("unit_test_state_machines", "history_test"))
    state_machine = storage.load_state_machine_from_path(state_machine_path)
    state_dict = {
        'Container': state_machine.get_state_by_path("CONT2"),
        'State1': state_machine.get_state_by_path("CONT2/STATE1"),
        'State2': state_machine.get_state_by_path("CONT2/STATE2"),
        'State3': state_machine.get_state_by_path("CONT2/STATE3"),
        'Nested': state_machine.get_state_by_path("CONT2/STATE3/NESTED"),
        'Nested2': state_machine.get_state_by_path("CONT2/STATE3/NESTED2")
    }
    return state_machine, state_dict


def prepare_state_machine_model(state_machine):
    import rafcon.gui.singleton
    rafcon.gui.singleton.state_machine_manager_model.state_machine_manager.add_state_machine(state_machine)
    testing_utils.wait_for_gui()
    rafcon.gui.singleton.state_machine_manager_model.selected_state_machine_id = state_machine.state_machine_id

    sm_m = rafcon.gui.singleton.state_machine_manager_model.state_machines[state_machine.state_machine_id]
    sm_m.history.fake = False
    sm_m.history.with_verbose = False
    return sm_m


def create_state_machine_m(with_gui=False):
    if with_gui:
        state_machine, state_dict = call_gui_callback(create_state_machine)
        state_machine_m = call_gui_callback(prepare_state_machine_model, state_machine)
    else:
        state_machine, state_dict = create_state_machine()
        state_machine_m = prepare_state_machine_model(state_machine)
    return state_machine_m, state_dict


def perform_history_action(operation, *args, **kwargs):
    """Perform an operation on a state + undo and redo it with consistency checks

    :param operation: The operation to be called
    :param args: The arguments for the operation
    :param kwargs: The keyword arguments for the operation
    :return: the result of the operation and the new state reference
    rtype: tuple(any, rafcon.core.states.state.State)
    """
    state = operation.__self__
    if not isinstance(state, State):
        state = state.parent
    state_path = state.get_path()
    parent = state.parent
    state_machine_m = rafcon.gui.singleton.state_machine_manager_model.get_selected_state_machine_model()
    sm_history = state_machine_m.history
    history_length = len(sm_history.modifications)
    origin_hash = parent.mutable_hash().hexdigest()
    additional_operations = 0
    if "additional_operations" in kwargs:
        additional_operations = kwargs["additional_operations"]
        del kwargs["additional_operations"]
    operation_result = operation(*args, **kwargs)
    assert len(sm_history.modifications) == history_length + 1 + additional_operations
    after_operation_hash = parent.mutable_hash().hexdigest()
    for _ in range(1 + additional_operations):
        sm_history.undo()
    undo_operation_hash = parent.mutable_hash().hexdigest()
    for _ in range(1 + additional_operations):
        sm_history.redo()
    redo_operation_hash = parent.mutable_hash().hexdigest()
    assert origin_hash == undo_operation_hash
    assert after_operation_hash == redo_operation_hash
    return operation_result, state_machine_m.state_machine.get_state_by_path(state_path)


def perform_multiple_undo_redo(number):
    state_machine_m = rafcon.gui.singleton.state_machine_manager_model.get_selected_state_machine_model()
    sm_history = state_machine_m.history
    origin_hash = state_machine_m.mutable_hash().hexdigest()
    for _ in range(number):
        sm_history.undo()
    for _ in range(number):
        sm_history.redo()
    final_hash = state_machine_m.mutable_hash().hexdigest()
    assert origin_hash == final_hash


def get_state_by_name(state_name, state_path_dict):
    state_machine_m = rafcon.gui.singleton.state_machine_manager_model.get_selected_state_machine_model()
    return state_machine_m.state_machine.get_state_by_path(state_path_dict[state_name])


def get_state_model_by_name(state_name, state_path_dict):
    state_machine_m = rafcon.gui.singleton.state_machine_manager_model.get_selected_state_machine_model()
    return state_machine_m.get_state_model_by_path(state_path_dict[state_name])


# TODO introduce test_add_remove_history with_gui=True to have a more reliable unit-test
def test_add_remove_history(caplog):
    testing_utils.dummy_gui(None)
    ##################
    # Root_state elements

    # add state
    # - change state
    # remove state
    # add outcome
    # - change outcome
    # remove outcome
    # add transition
    # - change transition
    # remove transition
    # add input_data_port
    # - change input_data_port
    # remove input_data_port
    # add output_data_port
    # - change output_data_port
    # remove output_data_port
    # add scoped_variable
    # - change scoped_variable
    # remove scoped_variable
    # add data_flow
    # - change data_flow
    # remove data_flow

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    state_machine_m, state_dict = create_state_machine_m()

    state_machine_path = TEST_PATH + '_test_add_remove'
    save_state_machine(state_machine_m, state_machine_path + '_before', logger, with_gui=False, menubar_ctrl=None)

    sm_history = state_machine_m.history

    state1 = HierarchyState('state1', state_id='STATE1')
    state2 = ExecutionState('state2', state_id='STATE2')

    state_dict['Nested'].add_state(state1)
    state_dict['Nested'].add_state(state2)
    state_dict['state1'] = state1
    state_dict['state2'] = state2

    state_path_dict = {}
    for key in state_dict:
        state_path_dict[key] = state_dict[key].get_path()

    def do_check_for_state(state_name):
        sm_history.modifications.reset()

        # Note: The elements always need to be retrieved before performing an operation, as undo/redo operations replace
        # both core and model objects
        state_m = get_state_model_by_name(state_name, state_path_dict)

        #############
        # outcome add & remove
        outcome_super, state = perform_history_action(state_m.state.add_outcome, "super")

        state = get_state_by_name(state_name, state_path_dict)
        _, state = perform_history_action(state_m.state.remove_outcome, outcome_super)


        #############
        # add two states
        state4 = ExecutionState('State4', state_id='STATE4')
        state_path_dict['state4'] = state.get_path() + "/" + "STATE4"
        _, state = perform_history_action(state.add_state, state4)

        state5 = ExecutionState('State5', state_id='STATE5')
        state_path_dict['state5'] = state.get_path() + "/" + "STATE5"
        _, state = perform_history_action(state.add_state, state5)

        perform_multiple_undo_redo(2)

        state4 = get_state_by_name('state4', state_path_dict)
        outcome_state4 = state4.add_outcome('UsedHere')
        assert len(sm_history.modifications) == 6

        state5 = get_state_by_name('state5', state_path_dict)
        outcome_state5 = state5.add_outcome('UsedHere')
        assert len(sm_history.modifications) == 7

        ################
        # add transition from_state_id, from_outcome, to_state_id=None, to_outcome=None, transition_id
        new_transition_id1, state = perform_history_action(state.add_transition,
                                                           from_state_id=state4.state_id, from_outcome=outcome_state4,
                                                           to_state_id=state5.state_id, to_outcome=None)
        _, state = perform_history_action(state.add_transition,
                                          from_state_id=state5.state_id, from_outcome=outcome_state5,
                                          to_state_id=state.state_id, to_outcome=-1)

        ###################
        # remove transition
        _, state = perform_history_action(state.remove_transition, new_transition_id1)

        #############
        # remove state
        _, state = perform_history_action(state.remove_state, state5.state_id)


        #############
        # add input_data_port
        state4 = get_state_by_name('state4', state_path_dict)
        input_state4_id, state4 = perform_history_action(state4.add_input_data_port, "input", "str", "zero")

        #############
        # remove input_data_port
        _, state4 = perform_history_action(state4.remove_input_data_port, input_state4_id)


        #############
        # add output_data_port
        output_state4_id, state4 = perform_history_action(state4.add_output_data_port, "output_"+state4.state_id, "int")

        #############
        # remove output_data_port
        _, state4 = perform_history_action(state4.remove_output_data_port, output_state4_id)


        # prepare again state4
        state4.add_output_data_port("output", "int")
        state4.add_input_data_port("input_new", "str", "zero")
        assert len(sm_history.modifications) == 17
        output_state4_id = state4.add_output_data_port("output_new", "int")
        assert len(sm_history.modifications) == 18

        state5 = ExecutionState('State5', 'STATE5')
        state = get_state_by_name(state_name, state_path_dict)
        state.add_state(state5)
        assert state_path_dict['state5'] == state5.get_path()
        assert len(sm_history.modifications) == 19
        input_par_state5 = state5.add_input_data_port("par", "int", 0)
        assert len(sm_history.modifications) == 20
        output_res_state5 = state5.add_output_data_port("res", "int")
        assert len(sm_history.modifications) == 21

        #####################
        # add scoped_variable
        scoped_buffer_nested, state = perform_history_action(state.add_scoped_variable, "buffer", "int")

        #####################
        # remove scoped_variable
        _, state = perform_history_action(state.remove_scoped_variable, scoped_buffer_nested)

        #############
        # add data_flow
        new_df_id, state = perform_history_action(state.add_data_flow,
                                                  from_state_id=state4.state_id, from_data_port_id=output_state4_id,
                                                  to_state_id=state5.state_id, to_data_port_id=input_par_state5)

        ################
        # remove data_flow
        perform_history_action(state.remove_data_flow, new_df_id)

    do_check_for_state(state_name='Nested')
    do_check_for_state(state_name='Container')

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_state_property_modifications_history(caplog):
    ##################
    # state properties
    # TODO LibraryState test for properties like mentioned in the notification-test but also general for add and remove

    # change name
    # change parent
    # change states
    # change outcomes
    # change transitions
    # change input_data_ports
    # change output_data_ports
    # change scoped_variables
    # change data_flows
    # change script
    # change script_text
    # change description
    # change active
    # set_start_state
    # change start_state_id
    # change child_execution

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()

    state1 = ExecutionState('State1', state_id="STATE1")
    state1.add_input_data_port("input", "str", "zero")
    state1.add_output_data_port("output", "int")
    state1.add_output_data_port("count", "int")

    state2 = ExecutionState('State2')
    state2.add_input_data_port("par", "int", 0)
    state2.add_input_data_port("number", "int", 5)
    state2.add_output_data_port("res", "int")

    nested_state = state_dict['Nested']
    nested_state_path = nested_state.get_path()
    nested_state.add_state(state1)
    nested_state.add_state(state2)
    state2_path = state2.get_path()
    nested_state.add_output_data_port("res", "int")

    state1.add_outcome("again")
    state1.add_outcome("counted")

    assert len(sm_model.history.modifications) == 6

    state2.add_outcome("done")
    state2.add_outcome("best")
    state2.add_outcome("full")
    assert len(sm_model.history.modifications) == 9

    nested_state.add_outcome("great")
    assert len(sm_model.history.modifications) == 10

    #######################################
    ######## Properties of State ##########

    # name(self, name)
    _, nested_state = perform_history_action(nested_state.__setattr__, "name", "nested")

    # TODO: The following commented operations are not correctly supported by the history!
    # input_data_ports(self, input_data_ports) None or dict
    # _, nested_state = perform_history_action(nested_state.__setattr__, "input_data_ports", {})
    # _, nested_state = perform_history_action(nested_state.__setattr__, "output_data_ports", {})

    # outcomes(self, outcomes) None or dict
    # _, nested_state = perform_history_action(nested_state.__setattr__, "outcomes", nested_state.outcomes)
    # _, nested_state = perform_history_action(nested_state.__setattr__, "outcomes", {})

    script_text = '\ndef execute(self, inputs, outputs, gvm):\n\tself.logger.debug("Hello World")\n\treturn 0\n'
    script_text1 = '\ndef execute(self, inputs, outputs, gvm):\n\tself.logger.debug("Hello NERD")\n\treturn 0\n'

    # script(self, script) Script -> no script setter any more only script_text !!!
    nested2_state = state_dict['Nested2']
    _, nested2_state = perform_history_action(nested2_state.__setattr__, "script_text", script_text)

    # script_text(self, script_text)
    _, nested2_state = perform_history_action(nested2_state.__setattr__, "script_text", script_text1)

    # description(self, description) str
    _, nested_state = perform_history_action(nested_state.__setattr__, "description", "awesome")

    ############################################
    ###### Properties of ContainerState ########

    # set_start_state(self, state) State or state_id
    _, nested_state = perform_history_action(nested_state.set_start_state, "STATE1")

    # set_start_state(self, start_state)
    state2 = sm_model.state_machine.get_state_by_path(state2_path)
    _, nested_state = perform_history_action(nested_state.set_start_state, state2, additional_operations=1)

    # transitions(self, transitions) None or dict
    _, nested_state = perform_history_action(nested_state.__setattr__, "transitions", {})

    # data_flows(self, data_flows) None or dict
    _, nested_state = perform_history_action(nested_state.__setattr__, "data_flows", {})

    # scoped_variables(self, scoped_variables) None or dict
    _, nested_state = perform_history_action(nested_state.__setattr__, "scoped_variables", {})

    # states(self, states) None or dict
    _, nested_state = perform_history_action(nested_state.__setattr__, "states", {})

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_outcome_property_modifications_history(caplog):
    ##################
    # outcome properties

    # change name

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()

    ####################################################
    # modify outcome and generate in previous a observer
    for state_name in ["Nested", "Nested2"]:
        state_path = state_dict[state_name].get_path()
        outcome_ids = list(state_dict[state_name].outcomes.keys())
        for outcome_id in outcome_ids:
            state = sm_model.state_machine.get_state_by_path(state_path)
            if outcome_id >= 0:
                outcome = state.outcomes[outcome_id]
                _, _ = perform_history_action(outcome.__setattr__, "name", "new_name_" + str(outcome_id))

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_transition_property_modifications_history(caplog):
    ##################
    # transition properties

    # change modify_origin
    # change from_outcome
    # change to_state
    # change to_outcome
    # modify_transition_from_state
    # modify_transition_from_outcome
    # modify_transition_to_outcome
    # modify_transition_to_state

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()

    state1 = ExecutionState('State1')
    outcome_again_state1 = state1.add_outcome("again")
    state2 = ExecutionState('State2')
    oc_done_state2 = state2.add_outcome("done")
    state2.add_outcome("best")
    nested_state = state_dict['Nested']
    nested_state.add_state(state1)
    nested_state.add_state(state2)
    nested_state.add_outcome("great")
    state1.add_outcome("counted")
    oc_full_state2 = state2.add_outcome("full")

    new_trans_id, nested_state = perform_history_action(nested_state.add_transition,
                                                        from_state_id=state1.state_id, from_outcome=outcome_again_state1,
                                                        to_state_id=state1.state_id, to_outcome=None)

    # modify_origin(self, from_state, from_outcome)
    _, nested_state = perform_history_action(nested_state.transitions[new_trans_id].modify_origin,
                                             from_state=state2.state_id, from_outcome=oc_full_state2)

    # from_outcome(self, from_outcome)
    _, nested_state = perform_history_action(nested_state.transitions[new_trans_id].__setattr__, "from_outcome",
                                             oc_done_state2)

    # to_state(self, to_state)
    _, nested_state = perform_history_action(nested_state.transitions[new_trans_id].__setattr__, "to_state",
                                             state2.state_id)

    # reset observer and testbed
    _, nested_state = perform_history_action(nested_state.remove_transition, new_trans_id)
    new_df_id, nested_state = perform_history_action(nested_state.add_transition,
                                                     from_state_id=state1.state_id, from_outcome=outcome_again_state1,
                                                     to_state_id=state1.state_id, to_outcome=None)

    _, nested_state = perform_history_action(nested_state.transitions[new_df_id].modify_origin,
                                             state2.state_id, oc_full_state2)

    # modify_transition_from_outcome(self, transition_id, from_outcome)
    _, nested_state = perform_history_action(nested_state.transitions[new_df_id].__setattr__, "from_outcome",
                                             oc_done_state2)

    # modify_transition_to_state(self, transition_id, to_state, to_outcome)
    _, nested_state = perform_history_action(nested_state.transitions[new_df_id].__setattr__, "to_state",
                                             state1.state_id)

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_input_port_modify(caplog):
    ##################
    # input_data_port properties

    # change name
    # change data_type
    # change default_value
    # change datatype

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()
    nested_state = state_dict['Nested2']

    new_input_data_port_id, nested_state = perform_history_action(nested_state.add_input_data_port,
                                                                  name='new_input', data_type='str')

    ################################
    # check for modification of name
    _, nested_state = perform_history_action(nested_state.input_data_ports[new_input_data_port_id].__setattr__, "name",
                                             "changed_new_input_name")

    #####################################
    # check for modification of data_type
    _, nested_state = perform_history_action(nested_state.input_data_ports[new_input_data_port_id].__setattr__,
                                             "data_type", "int")

    #########################################
    # check for modification of default_value
    _, nested_state = perform_history_action(nested_state.input_data_ports[new_input_data_port_id].__setattr__,
                                             "default_value", 5)

    ###########################################
    # check for modification of change_datatype
    _, nested_state = perform_history_action(nested_state.input_data_ports[new_input_data_port_id].change_data_type,
                                             data_type='str', default_value='awesome_tool')

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_output_port_modify(caplog):

    ##################
    # output_data_port properties

    # change name
    # change data_type
    # change default_value
    # change datatype
    # create testbed

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()
    nested_state = state_dict['Nested2']

    new_output_data_port_id, nested_state = perform_history_action(nested_state.add_output_data_port,
                                                                   name='new_output', data_type='str')

    ################################
    # check for modification of name
    _, nested_state = perform_history_action(nested_state.output_data_ports[new_output_data_port_id].__setattr__, "name",
                                             "changed_new_output_name")

    #####################################
    # check for modification of data_type
    _, nested_state = perform_history_action(nested_state.output_data_ports[new_output_data_port_id].__setattr__,
                                             "data_type", "int")

    #########################################
    # check for modification of default_value
    _, nested_state = perform_history_action(nested_state.output_data_ports[new_output_data_port_id].__setattr__,
                                             "default_value", 5)

    ###########################################
    # check for modification of change_datatype
    _, nested_state = perform_history_action(nested_state.output_data_ports[new_output_data_port_id].change_data_type,
                                             data_type='str', default_value='awesome_tool')


    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_scoped_variable_modify(caplog):
    ##################
    # scoped_variable properties

    # change name
    # change data_type
    # change default_value
    # change datatype
    # create testbed

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()

    nested_state = state_dict['Nested']

    new_scoped_variable_id, nested_state = perform_history_action(nested_state.add_scoped_variable,
                                                                   name='new_output', data_type='str')

    ################################
    # check for modification of name
    _, nested_state = perform_history_action(nested_state.scoped_variables[new_scoped_variable_id].__setattr__,
                                             "name",
                                             "changed_new_scoped_var_name")

    #####################################
    # check for modification of data_type
    _, nested_state = perform_history_action(nested_state.scoped_variables[new_scoped_variable_id].__setattr__,
                                             "data_type", "int")

    #########################################
    # check for modification of default_value
    _, nested_state = perform_history_action(nested_state.scoped_variables[new_scoped_variable_id].__setattr__,
                                             "default_value", 5)

    ###########################################
    # check for modification of change_datatype
    _, nested_state = perform_history_action(nested_state.scoped_variables[new_scoped_variable_id].change_data_type,
                                             data_type='str', default_value='awesome_tool')

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


def test_data_flow_property_modifications(caplog):
    ##################
    # data_flow properties

    # change modify_origin
    # change from_key
    # change modify_target
    # change to_key
    # modify_transition_from_state
    # modify_transition_from_key
    # modify_transition_to_key
    # modify_transition_to_state

    testing_utils.dummy_gui(None)

    testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False,
                                                     'HISTORY_ENABLED': True}, gui_already_started=False)
    sm_model, state_dict = create_state_machine_m()
    nested_state = state_dict['Nested']

    state1 = ExecutionState('State1')
    output_state1 = state1.add_output_data_port("output", "int")
    state1.add_input_data_port("input", "str", "zero")
    state2 = ExecutionState('State2')
    input_par_state2 = state2.add_input_data_port("par", "int", 0)
    output_res_state2 = state2.add_output_data_port("res", "int")
    nested_state.add_state(state1)
    nested_state.add_state(state2)
    output_res_nested = nested_state.add_output_data_port("res", "int")
    output_count_state1 = state1.add_output_data_port("count", "int")
    input_number_state2 = state2.add_input_data_port("number", "int", 5)

    new_df_id, nested_state = perform_history_action(nested_state.add_data_flow,
                                                     from_state_id=state2.state_id, from_data_port_id=output_res_state2,
                                                     to_state_id=nested_state.state_id, to_data_port_id=output_res_nested)

    ##### modify from data_flow #######
    # modify_origin(self, from_state, from_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].modify_origin,
                                             from_state=state1.state_id, from_key=output_state1)

    # from_key(self, from_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].__setattr__, "from_key",
                                             output_count_state1)

    # modify_target(self, to_state, to_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].modify_target,
                                             to_state=state2.state_id, to_key=input_par_state2)

    # to_key(self, to_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].__setattr__, "to_key",
                                             input_number_state2)

    # reset observer and testbed
    _, nested_state = perform_history_action(nested_state.remove_data_flow, new_df_id)

    new_df_id, nested_state = perform_history_action(nested_state.add_data_flow,
                                                     from_state_id=state2.state_id, from_data_port_id=output_res_state2,
                                                     to_state_id=nested_state.state_id, to_data_port_id=output_res_nested)

    ##### modify from parent state #######
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].modify_origin,
                                             state1.state_id, output_state1)

    # modify_data_flow_from_key(self, data_flow_id, from_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].__setattr__, "from_key",
                                             output_count_state1)

    # modify_data_flow_to_state(self, data_flow_id, to_state, to_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].modify_target,
                                             state2.state_id, input_par_state2)

    # modify_data_flow_to_key(self, data_flow_id, to_key)
    _, nested_state = perform_history_action(nested_state.data_flows[new_df_id].__setattr__, "to_key",
                                             input_number_state2)

    testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)


@pytest.mark.timeout(60)
@pytest.mark.parametrize("with_gui", [False, True])
def test_state_type_changes_with_gui(with_gui, caplog):

    testing_utils.dummy_gui(None)

    if with_gui:
        testing_utils.run_gui(gui_config={'AUTO_BACKUP_ENABLED': False, 'HISTORY_ENABLED': True})
        e = None
        try:
            trigger_state_type_change_tests(with_gui)
        except Exception as e:
            pass
        finally:
            testing_utils.close_gui()
            if e:
                logging.exception("Test failed with exception {0}".format(e))
            testing_utils.shutdown_environment(caplog=caplog)
    else:
        testing_utils.initialize_environment(gui_config={'AUTO_BACKUP_ENABLED': False, 'HISTORY_ENABLED': True},
                                             gui_already_started=False)
        trigger_state_type_change_tests(with_gui)
        testing_utils.shutdown_environment(caplog=caplog, unpatch_threading=False)



def test_state_type_change_bugs_with_gui(caplog):

    testing_utils.dummy_gui(None)

    testing_utils.run_gui(gui_config={'AUTO_BACKUP_ENABLED': False, 'HISTORY_ENABLED': True})
    try:
        trigger_state_type_change_typical_bug_tests()
    except:
        raise  # required, otherwise the exception cannot be accessed within finally
    finally:
        testing_utils.close_gui()
        testing_utils.shutdown_environment(caplog=caplog)


def test_multiple_undo_redo_bug_with_gui(caplog):
    testing_utils.run_gui(gui_config={'AUTO_BACKUP_ENABLED': True, 'HISTORY_ENABLED': True})
    try:
        trigger_multiple_undo_redo_bug_tests(with_gui=True)
    finally:
        testing_utils.close_gui()
        # Do not pass caplog, as we want to ignore all warnings (we cannot predict how many will occur)
        testing_utils.shutdown_environment()


def do_type_change(state_machine_m, state_m, target_state_type, with_gui):
    if not with_gui:
        import rafcon.gui.helpers.state as gui_helper_state
        gui_helper_state.change_state_type(state_m, target_state_type)
    else:
        import rafcon.gui.singleton
        main_window_controller = rafcon.gui.singleton.main_window_controller
        [state_editor_ctrl, list_store_id_from_state_type_dict] = \
            get_state_editor_ctrl_and_store_id_dict(state_machine_m, state_m, main_window_controller, 5., logger)
        # - do state type change
        state_type_row_id = list_store_id_from_state_type_dict[target_state_type.__name__]
        state_editor_ctrl.get_controller('properties_ctrl').view['type_combobox'].set_active(state_type_row_id)


def trigger_state_type_change_tests(with_gui):

    def perform_operation(with_gui, operation, *args, **kwargs):
        if with_gui:
            return call_gui_callback(operation, *args, **kwargs)
        return operation(*args, **kwargs)

    def perform_state_type_change(state_path, state_machine_m, target_state_type, with_gui, open_all_states=False):
        original_state_m = sm_m.get_state_model_by_path(state_path)

        def parental_hash(state_m):
            parent_m = state_m.parent or sm_m
            return parent_m.mutable_hash().hexdigest()

        logger.info("State type change from {} to {}".format(original_state_m.state.__class__.__name__, target_state_type.__name__))
        parent_state_m_hash = parental_hash(original_state_m)
        perform_operation(with_gui, do_type_change, state_machine_m, original_state_m, target_state_type, with_gui)
        type_changed_state_m = state_machine_m.get_state_model_by_path(state_path)
        parent_changed_state_m_hash = parental_hash(type_changed_state_m)

        logger.info("Undo state type change")
        perform_operation(with_gui, state_machine_m.history.undo)
        undone_state_m = state_machine_m.get_state_model_by_path(state_path)
        assert parent_state_m_hash == parental_hash(undone_state_m), "Model hash not identical"

        if with_gui and open_all_states:
            call_gui_callback(select_child_states_and_state_sequentially, state_machine_m, undone_state_m, logger)

        logger.info("Redo state type change")
        perform_operation(with_gui, state_machine_m.history.redo)
        redone_state_m = state_machine_m.get_state_model_by_path(state_path)
        assert parent_changed_state_m_hash == parental_hash(redone_state_m), "Model hash not identical"

        if with_gui and open_all_states:
            call_gui_callback(select_child_states_and_state_sequentially, state_machine_m, redone_state_m, logger)

        return redone_state_m

    sm_m, state_dict = create_state_machine_m(with_gui)
    perform_operation(with_gui, sm_m.history.modifications.reset)

    ####### General Type Change of CHILD STATE ############
    state_name = 'State3'
    state_path = state_dict[state_name].get_path()

    # Select state
    state_m = sm_m.get_state_model_by_path(state_path)
    perform_operation(with_gui, sm_m.selection.set, [state_m])

    type_change_order = [BarrierConcurrencyState, HierarchyState, PreemptiveConcurrencyState, ExecutionState]
    for i, state_class in enumerate(type_change_order):
        perform_state_type_change(state_path, sm_m, state_class, with_gui, i == 0)

    ####### General Type Change of ROOT STATE ############
    state_name = 'Container'
    state_path = state_dict[state_name].get_path()

    # Select state
    state_m = sm_m.get_state_model_by_path(state_path)
    perform_operation(with_gui, sm_m.selection.set, [state_m])

    for i, state_class in enumerate(type_change_order):
        perform_state_type_change(state_path, sm_m, state_class, with_gui, i == 0)


def trigger_state_type_change_typical_bug_tests():
    import rafcon.gui.singleton
    sm_manager_model = rafcon.gui.singleton.state_machine_manager_model

    sm_m, state_dict = create_state_machine_m(True)

    check_elements_ignores.append("internal_transitions")

    # General Type Change inside of a state machine (NO ROOT STATE) ############
    state_of_type_change = 'State3'
    parent_of_type_change = 'Container'

    # do state_type_change with gui
    call_gui_callback(sm_m.history.modifications.reset)

    state_m = sm_m.get_state_model_by_path(state_dict[state_of_type_change].get_path())
    call_gui_callback(sm_m.selection.set, [state_m])

    current_sm_length = len(sm_manager_model.state_machines)
    # print "1:", sm_manager_model.state_machines.keys()
    logger.debug('number of sm is : {0}'.format(sm_manager_model.state_machines.keys()))

    def create_state_machine():
        root_state = HierarchyState("new root state", state_id="ROOT")
        state_machine = StateMachine(root_state)
        sm_manager_model.state_machine_manager.add_state_machine(state_machine)
        return state_machine

    state_machine = call_gui_callback(create_state_machine)

    logger.debug('number of sm is : {0}'.format(sm_manager_model.state_machines.keys()))
    assert len(sm_manager_model.state_machines) == current_sm_length+1
    sm_m = sm_manager_model.state_machines[state_machine.state_machine_id]
    h_state1 = HierarchyState(state_id='HSTATE1')
    call_gui_callback(sm_m.state_machine.root_state.add_state, h_state1)
    h_state2 = HierarchyState(state_id='HSTATE2')
    call_gui_callback(h_state1.add_state, h_state2)
    ex_state1 = ExecutionState(state_id='EXSTATE1')
    call_gui_callback(h_state1.add_state, ex_state1)
    ex_state2 = ExecutionState(state_id='EXSTATE2')
    call_gui_callback(h_state2.add_state, ex_state2)

    logger.info("DO_TYPE_CHANGE")
    h_state1_m = sm_m.get_state_model_by_path(h_state1.get_path())
    call_gui_callback(do_type_change, sm_m, h_state1_m, ExecutionState, True)

    logger.info("UNDO")
    call_gui_callback(sm_m.history.undo)

    logger.info("UNDO finished")
    logger.info("REDO")
    call_gui_callback(sm_m.history.redo)
    logger.info("REDO finished")

    check_elements_ignores.remove("internal_transitions")
    print(check_elements_ignores)


def trigger_multiple_undo_redo_bug_tests(with_gui=False):
    from gi.repository import GLib
    import rafcon.gui.singleton
    sm = StateMachine(HierarchyState())
    call_gui_callback(rafcon.core.singleton.state_machine_manager.add_state_machine, sm)
    sm_m = list(rafcon.gui.singleton.state_machine_manager_model.state_machines.values())[-1]
    call_gui_callback(sm_m.selection.set, [sm_m.root_state])

    main_window_controller = rafcon.gui.singleton.main_window_controller
    sm_id = sm_m.state_machine.state_machine_id
    state_machines_editor_ctrl = main_window_controller.get_controller('state_machines_editor_ctrl')
    state_machines_editor_ctrl.get_controller(sm_id).view.get_top_widget().grab_focus()
    state_machines_editor_ctrl.get_controller(sm_id).view.editor.grab_focus()
    def trigger_action_repeated(action_name, number):
        for _ in range(number):
            main_window_controller.shortcut_manager.trigger_action(action_name, None, None)
    call_gui_callback(trigger_action_repeated, "add", 10, priority=GLib.PRIORITY_HIGH)
    assert sm_m.history.modifications.get_executed_history_ids()
    call_gui_callback(trigger_action_repeated, "undo", 20, priority=GLib.PRIORITY_HIGH)
    call_gui_callback(trigger_action_repeated, "redo", 20, priority=GLib.PRIORITY_HIGH)


def test_reset_to_history_id(caplog):
    testing_utils.run_gui(gui_config={'HISTORY_ENABLED': True})
    try:
        state_machine_m, state_dict = create_state_machine_m(True)
        sm_history = state_machine_m.history

        state1_id = state_dict["State1"].state_id
        state2_id = state_dict["State2"].state_id

        # Remove two child states of root state
        call_gui_callback(state_machine_m.root_state.state.remove_state, state1_id)  # history id 1
        call_gui_callback(state_machine_m.root_state.state.remove_state, state2_id)  # history id 2
        history_2_hash = state_machine_m.mutable_hash().hexdigest()

        # Undo removal
        call_gui_callback(state_machine_m.history.undo)
        call_gui_callback(state_machine_m.history.undo)

        # Create new history branch by adding two new states
        state4 = ExecutionState("State4")
        state5 = ExecutionState("State5")

        call_gui_callback(state_machine_m.root_state.state.add_state, state4)  # history id 3
        call_gui_callback(state_machine_m.root_state.state.add_state, state5)  # history id 4

        assert state_machine_m.history.modifications.current_history_element.history_id == 4
        history_4_hash = state_machine_m.mutable_hash().hexdigest()

        # Activate old branch
        call_gui_callback(state_machine_m.history.reset_to_history_id, 2)
        reset_history_2_hash = state_machine_m.mutable_hash().hexdigest()
        assert history_2_hash == reset_history_2_hash

        # Activate new branch
        call_gui_callback(state_machine_m.history.reset_to_history_id, 4)
        reset_history_4_hash = state_machine_m.mutable_hash().hexdigest()
        assert history_4_hash == reset_history_4_hash

    finally:
        testing_utils.close_gui()
        testing_utils.shutdown_environment(caplog=caplog)


if __name__ == '__main__':
    testing_utils.dummy_gui(None)
    # test_add_remove_history(None)
    # test_state_property_modifications_history(None)
    #
    # test_outcome_property_modifications_history(None)
    # test_input_port_modify_notification(None)
    # test_output_port_modify_notification(None)
    #
    # test_transition_property_modifications_history(None)
    # test_scoped_variable_modify_notification(None)
    # test_data_flow_property_modifications_history(None)

    # test_state_type_changes_with_gui(with_gui=True, caplog=None)
    # test_state_type_change_bugs_with_gui(with_gui=False, caplog=None)
    # test_state_type_change_bugs_with_gui(with_gui=True, caplog=None)
    test_multiple_undo_redo_bug_with_gui(None)
    # pytest.main(['-xs', __file__])
