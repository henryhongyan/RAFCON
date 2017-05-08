#!/usr/bin/env python

# Copyright (C) 2015-2017 DLR
#
# All rights reserved. This program and the accompanying materials are made
# available under the terms of the Eclipse Public License v1.0 which
# accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
#
# Contributors:
# Annika Wollschlaeger <annika.wollschlaeger@dlr.de>
# Benno Voggenreiter <benno.voggenreiter@dlr.de>
# Franz Steinmetz <franz.steinmetz@dlr.de>
# Michael Vilzmann <michael.vilzmann@dlr.de>
# Rico Belder <rico.belder@dlr.de>
# Sebastian Brunner <sebastian.brunner@dlr.de>

"""
.. module: a module to enable state machine execution from the command line
   :synopsis: A module to start arbitrary state machines without the GUI and several configurations options

"""

import os
import argparse
from os.path import realpath, dirname, join, exists
import signal
import time
from Queue import Empty
import threading
import sys

import rafcon
from yaml_configuration.config import config_path
import rafcon.utils.filesystem as filesystem

from rafcon.core.config import global_config
import rafcon.core.singleton as core_singletons
from rafcon.core.storage import storage
from rafcon.core.states.state import StateExecutionStatus

from rafcon.utils import profiler
from rafcon.utils import plugins
from rafcon.utils import log

logger = log.get_logger("rafcon.start.core")

_user_abort = False


def pre_setup_plugins():
    """Loads plugins and calls the pre init hooks
    """
    plugins.load_plugins()
    plugins.run_pre_inits()


def post_setup_plugins(parser_result):
    """Calls the post init hubs

    :param dict parser_result: Dictionary with the parsed arguments
    """
    if not isinstance(parser_result, dict):
        parser_result = vars(parser_result)
    plugins.run_post_inits(parser_result)


def setup_environment():
    """Ensures that the environmental variables RAFCON_PATH and RAFCON_LIB_PATH are existent
    """
    rafcon_root_path = dirname(realpath(rafcon.__file__))
    if not os.environ.get('RAFCON_PATH', None):
        # set env variable RAFCON_PATH to the root directory of RAFCON
        os.environ['RAFCON_PATH'] = rafcon_root_path

    if not os.environ.get('RAFCON_LIB_PATH', None):
        # set env variable RAFCON_LIB_PATH to the library directory of RAFCON (when not using RMPM)
        os.environ['RAFCON_LIB_PATH'] = join(dirname(dirname(rafcon_root_path)), 'share', 'libraries')


def parse_state_machine_path(path):
    """Parser for argparse checking pfor a proper state machine path

    :param str path: Input path from the user
    :return: The path
    :raises argparse.ArgumentTypeError: if the path does not contain a statemachine.json file
    """
    sm_root_file = join(path, storage.STATEMACHINE_FILE)
    if exists(sm_root_file):
        return path
    else:
        sm_root_file = join(path, storage.STATEMACHINE_FILE_OLD)
        if exists(sm_root_file):
            return path
        raise argparse.ArgumentTypeError("Failed to open {0}: {1} not found in path".format(path,
                                                                                            storage.STATEMACHINE_FILE))


def setup_argument_parser():
    """Sets up teh parser with the required arguments

    :return: The parser object
    """
    home_path = filesystem.get_home_path()

    parser = core_singletons.argument_parser
    parser.add_argument('-o', '--open', type=parse_state_machine_path, dest='state_machine_path', metavar='path',
                        nargs='+', help="specify directories of state-machines that shall be opened. The path must "
                                        "contain a statemachine.json file")
    parser.add_argument('-c', '--config', type=config_path, metavar='path', dest='config_path', default=home_path,
                        nargs='?', const=home_path,
                        help="path to the configuration file config.yaml. Use 'None' to prevent the generation of "
                             "a config file and use the default configuration. Default: {0}".format(home_path))
    parser.add_argument('--remote', action='store_true', default=False, help="Remote Control Mode")
    parser.add_argument('-s', '--start_state_path', metavar='path', dest='start_state_path',
                        default=None, nargs='?', help="path of to the state that should be launched")
    return parser


def setup_configuration(config_path):
    """Loads the core configuration from the specified path and uses its content for further setup

    :param config_path: Path to the core config file
    """
    if config_path is not None:
        config_path, config_file = filesystem.separate_folder_path_and_file_name(config_path)
        global_config.load(config_file=config_file, path=config_path)
    else:
        global_config.load(path=config_path)

    # Initialize libraries
    core_singletons.library_manager.initialize()


def open_state_machine(state_machine_path):
    """Executes the specified state machine

    :param str state_machine_path: The file path to the state machine
    :return StateMachine: The loaded state machine
    """
    sm = storage.load_state_machine_from_path(state_machine_path)
    core_singletons.state_machine_manager.add_state_machine(sm)

    return sm


def start_state_machine(sm, start_state_path=None):
    core_singletons.state_machine_manager.active_state_machine_id = sm.state_machine_id
    core_singletons.state_machine_execution_engine.start(start_state_path=start_state_path)

    if reactor_required():
        sm_thread = threading.Thread(target=stop_reactor_on_state_machine_finish, args=[sm, ])
        sm_thread.start()


def wait_for_state_machine_finished(state_machine):
    """ wait for a state machine to finish its execution

    :param state_machine: the statemachine to synchronize with
    :return:
    """
    global _user_abort

    from rafcon.core.states.execution_state import ExecutionState
    if not isinstance(state_machine.root_state, ExecutionState):
        while len(state_machine.execution_histories[0]) < 1:
            time.sleep(0.1)
    else:
        time.sleep(0.5)

    while state_machine.root_state.state_execution_status is not StateExecutionStatus.INACTIVE:
        try:
            state_machine.root_state.concurrency_queue.get(timeout=1)
            # this check triggers if the state machine could not be stopped in the signal handler
            if _user_abort:
                return
        except Empty:
            pass
        # no logger output here to make it easier for the parser
        print "RAFCON live signal"


def stop_reactor_on_state_machine_finish(state_machine):
    """ Wait for a state machine to be finished and stops the reactor

    :param state_machine: the state machine to synchronize with
    """
    wait_for_state_machine_finished(state_machine)
    from twisted.internet import reactor
    if reactor.running:
        plugins.run_hook("pre_destruction")
        reactor.callFromThread(reactor.stop)


def reactor_required():
    if "twisted" in sys.modules.keys():
        return True
    return False


def signal_handler(signal, frame):
    global _user_abort

    from rafcon.core.execution.execution_status import StateMachineExecutionStatus
    state_machine_execution_engine = core_singletons.state_machine_execution_engine
    core_singletons.shut_down_signal = signal

    logger.info("Shutting down ...")

    try:
        if not state_machine_execution_engine.finished_or_stopped():
            state_machine_execution_engine.stop()
            state_machine_execution_engine.join(3)  # Wait max 3 sec for the execution to stop
    except Exception:
        logger.exception("Could not stop state machine")

    _user_abort = True

    # shutdown twisted correctly
    if reactor_required():
        from twisted.internet import reactor
        if reactor.running:
            plugins.run_hook("pre_destruction")
            reactor.callFromThread(reactor.stop)


def register_signal_handlers(callback):
    signal.signal(signal.SIGINT, callback)
    signal.signal(signal.SIGHUP, callback)
    signal.signal(signal.SIGQUIT, callback)
    signal.signal(signal.SIGTERM, callback)


def main():
    register_signal_handlers(signal_handler)

    logger.info("initialize RAFCON ... ")

    pre_setup_plugins()

    setup_environment()

    logger.info("parse arguments ... ")
    parser = setup_argument_parser()
    user_input = parser.parse_args()
    if not user_input.state_machine_path:
        logger.error("You have to specify a valid state machine path")
        exit(-1)

    setup_configuration(user_input.config_path)

    post_setup_plugins(user_input)

    if global_config.get_config_value("PROFILER_RUN", False):
        profiler.start("global")

    try:

        first_sm = None
        for sm_path in user_input.state_machine_path:
            sm = open_state_machine(sm_path)
            if first_sm is None:
                first_sm = sm

        if not user_input.remote:
            start_state_machine(first_sm, user_input.start_state_path)

        if reactor_required():
            from twisted.internet import reactor

            # Blocking call, return when state machine execution finishes
            reactor.run()

        if not user_input.remote:
            wait_for_state_machine_finished(first_sm)
        else:
            while not _user_abort:
                time.sleep(1)

        logger.info("State machine execution finished!")
        plugins.run_hook("post_destruction")
    finally:
        if global_config.get_config_value("PROFILER_RUN", False):
            result_path = global_config.get_config_value("PROFILER_RESULT_PATH")
            view = global_config.get_config_value("PROFILER_VIEWER")
            profiler.stop("global", result_path, view)


if __name__ == '__main__':
    main()