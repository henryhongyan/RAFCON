"""
.. module:: singleton
   :platform: Unix, Windows
   :synopsis: A module to hold all singletons of the state machine

.. moduleauthor:: Sebastian Brunner


"""

from awesome_tool.statemachine.global_variable_manager import GlobalVariableManager
from awesome_tool.statemachine.library_manager import LibraryManager
from awesome_tool.statemachine.execution.statemachine_execution_engine import StatemachineExecutionEngine
from awesome_tool.statemachine.storage.storage import StateMachineStorage
from awesome_tool.statemachine.state_machine_manager import StateMachineManager
from awesome_tool.statemachine.execution.statemachine_status import ExecutionMode

import sys


def signal_handler(signal, frame):
    print('SIGINT received! Execution engine will be stopped and program will be shutdown!')
    if state_machine_execution_engine.status.execution_mode is not ExecutionMode.STOPPED:
        state_machine_execution_engine.stop()
        active_state_machine_id = state_machine_execution_engine.state_machine_manager.active_state_machine_id
        state_machine_execution_engine.state_machine_manager.state_machines[active_state_machine_id].root_state.join()
    sys.exit(0)

# This variable holds the global variable manager singleton
global_variable_manager = GlobalVariableManager()

# This variable holds the library manager singleton
library_manager = LibraryManager()

# This variable holds the global state machine manager object
state_machine_manager = StateMachineManager()

# This variable holds the execution engine singleton
state_machine_execution_engine = StatemachineExecutionEngine(state_machine_manager)

# This variable holds a global storage object
global_storage = StateMachineStorage("")