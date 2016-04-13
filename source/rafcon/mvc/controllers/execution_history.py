"""
.. module:: execution_history
   :platform: Unix, Windows
   :synopsis: A module holds a controller for ExecutionHistoryView (list/tree) and offers information about the
     execution.

.. moduleauthor:: Sebastian Brunner


"""

import gtk
import gobject
import threading

import rafcon
from rafcon.mvc.controllers.extended_controller import ExtendedController
from rafcon.statemachine.state_machine_manager import StateMachineManager
from rafcon.statemachine.execution.execution_history import ConcurrencyItem, CallItem
from rafcon.utils import log

from rafcon.statemachine.singleton import state_machine_execution_engine

from rafcon.statemachine.enums import StateMachineExecutionStatus

logger = log.get_logger(__name__)


class ExecutionHistoryTreeController(ExtendedController):  # (Controller):
    """Controller handling the execution history.

    :param rafcon.mvc.models.state_machine_manager.StateMachineManagerModel model: The state machine manager model,
        holding data regarding state machines.
    :param rafcon.mvc.views.execution_history.ExecutionHistoryTreeView view: The GTK View showing the execution history
        tree.
    :param rafcon.statemachine.state_machine_manager.StateMachineManager state_machine_manager:
    """

    def __init__(self, model=None, view=None, state_machine_manager=None):
        ExtendedController.__init__(self, model, view)
        self.history_tree_store = gtk.TreeStore(str, gobject.TYPE_PYOBJECT)
        self.history_tree = view['history_tree']
        self.history_tree.set_model(self.history_tree_store)

        assert isinstance(state_machine_manager, StateMachineManager)
        self.state_machine_manager = state_machine_manager

        view['reload_button'].connect('clicked', self.reload_history)

        self.state_machine_execution_model = rafcon.mvc.singleton.state_machine_execution_model
        self.observe_model(self.state_machine_execution_model)

        self.update()

    def register_adapters(self):
        pass

    def register_view(self, view):
        self.history_tree.connect('button_press_event', self.right_click)

    def switch_state_machine_execution_manager_model(self, new_state_machine_execution_engine):
        """
        Switch the state machine execution engine model to observe.
        :param new_state_machine_execution_engine: the new sm execution engine manager model
        :return:
        """
        self.relieve_model(self.state_machine_execution_model)
        self.state_machine_execution_model = new_state_machine_execution_engine
        self.observe_model(self.state_machine_execution_model)

    def right_click(self, widget, event=None):
        """Triggered when right click is pressed in the history tree.
        """
        if event.type == gtk.gdk.BUTTON_PRESS and event.button == 3:
            x = int(event.x)
            y = int(event.y)
            time = event.time
            pthinfo = self.history_tree.get_path_at_pos(x, y)
            if pthinfo is not None:
                path, col, cellx, celly = pthinfo
                self.history_tree.grab_focus()
                self.history_tree.set_cursor(path, col, 0)

                popup_menu = gtk.Menu()

                model, row = self.history_tree.get_selection().get_selected()
                scoped_data = model[row][1]
                if scoped_data is None:
                    return
                for key, data in scoped_data.iteritems():
                    menu_item_string = "%s (%s - %s):\t%s" % (data.name, key, data.value_type, data.value)
                    menu_item = gtk.MenuItem(menu_item_string)
                    menu_item.set_sensitive(False)
                    menu_item.show()
                    popup_menu.append(menu_item)

                popup_menu.show()

                popup_menu.popup(None, None, None, event.button, time)
            return True

    # @ExtendedController.observe("execution_history", after=True)
    # def model_changed(self, model, prop_name, info):
    #     logger.warning("execution_history changed")
    #     print info
    #     #self.update()  # TODO: only update when execution mode is not RUNNING (while running history not interesting)
    #                     # TODO: update when finished RUNNING all states or other state activated

    @ExtendedController.observe("execution_engine", after=True)
    def execution_history_focus(self, model, prop_name, info):
        """ Arranges to put execution-history widget page to become top page in notebook when execution starts and stops
        and resets the boolean of modification_history_was_focused to False each time this notification are observed.
        """
        from rafcon.mvc.utils.notification_overview import NotificationOverview
        overview = NotificationOverview(info)
        # logger.info("execution_engine runs method '{1}' and has status {0}"
        #             "".format(str(state_machine_execution_engine.status.execution_mode).split('.')[-1],
        #                       overview['method_name'][-1]))
        if state_machine_execution_engine.status.execution_mode in \
                [StateMachineExecutionStatus.STARTED, StateMachineExecutionStatus.STOPPED]:
            if self.parent is not None and hasattr(self.parent, "focus_notebook_page_of_controller"):
                # request focus -> which has not have to be satisfied
                self.parent.focus_notebook_page_of_controller(self)

        if state_machine_execution_engine.status.execution_mode is not StateMachineExecutionStatus.STARTED:
            self.update()

    def reload_history(self, widget, event=None):
        """Triggered when the 'Reload History' button is clicked."""
        self.update()

    def update(self):
        self.history_tree_store.clear()
        active_sm = self.state_machine_manager.get_active_state_machine()
        if not active_sm:
            return
        execution_history_items = active_sm.execution_history.history_items
        for item in execution_history_items:
            if isinstance(item, ConcurrencyItem):
                self.insert_rec(None, item.state_reference.name, item.execution_histories, None)
            elif isinstance(item, CallItem):
                self.insert_rec(None, item.state_reference.name + " - Call", None, item.scoped_data)
            else:
                self.insert_rec(None, item.state_reference.name + " - Return", None, item.scoped_data)

    def insert_rec(self, parent, history_item_name, history_item_children, history_item_scoped_data):
        tree_item = self.history_tree_store.insert_after(parent, None, (history_item_name, history_item_scoped_data))
        if isinstance(history_item_children, dict):
            for child_history_number, child_history in history_item_children.iteritems():
                for item in child_history.history_items:
                    if isinstance(item, ConcurrencyItem):
                        self.insert_rec(tree_item, item.state_reference.name, item.execution_histories, None)
                    elif isinstance(item, CallItem):
                        self.insert_rec(tree_item, item.state_reference.name + " - Call", None, item.scoped_data)
                    else:
                        self.insert_rec(tree_item, item.state_reference.name + " - Return", None, item.scoped_data)
