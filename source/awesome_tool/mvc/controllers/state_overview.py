import gtk
from gtkmvc import View, Controller

from statemachine.states.state import State, StateType
from statemachine.states.container_state import ContainerState
from statemachine.states.concurrency_state import ConcurrencyState
from statemachine.states.preemptive_concurrency_state import PreemptiveConcurrencyState


class StateOverviewController(Controller):
    """Controller handling the view of properties/attributes of the ContainerStateModel

    This :class:`gtkmvc.Controller` class is the interface between the GTK widget view
    :class:`mvc.views.source_editor.SourceEditorView` and the properties of the
    :class:`mvc.models.state.StateModel`. Changes made in
    the GUI are written back to the model and vice versa.

    :param mvc.models.StateModel model: The state model containing the data
    :param mvc.views.SourceEditorView view: The GTK view showing the data as a table
    """

    # TODO Missing functions

    def __init__(self, model, view):
        """Constructor
        """
        Controller.__init__(self, model, view)

    def register_view(self, view):
        """Called when the View was registered

        Can be used e.g. to connect signals. Here, the destroy signal is connected to close the application
        """
        # StateType = Enum('STATE_TYPE', 'EXECUTION HIERARCHY BARRIER_CONCURRENCY PREEMPTION_CONCURRENCY LIBRARY')
        # self.state_types_dict = {}
        # self.state_types_dict[type(State)] = {'Enum': StateType.STATE_TYPE}
        # self.state_types_dict[type(ContainerState)] = {'Enum': StateType.STATE_TYPE}
        view['entry_name'].connect('focus-out-event', self.change_name)
        view['description_textview'].connect('focus-out-event', self.change_description)
        view['entry_name'].set_text(self.model.state.name)
        view['label_id_value'].set_text(self.model.state.state_id)
        view['label_type_value'].set_text(str(self.model.state.state_type))
        view['description_textview'].set_buffer(self.model.state.description)
        #view['state_type_combo'].set_buffer(self.model.state.description)

    #def on_window_state_editor_destroy(self):
    #    gtk.main_quit()

    def register_adapters(self):
        """Adapters should be registered in this method call

        Each property of the state should have its own adapter, connecting a label in the View with the attribute of
        the State.
        """
        #self.adapt(self.__state_property_adapter("name", "input_name"))

    def change_name(self, entry, otherwidget):
        entry_text = entry.get_text()
        # logger.debug("State %s changed name from '%s' to: '%s'\n" % (self.model.state.state_id,
        #                                                              self.model.state.name, entry_text))
        self.model.state.name = entry_text
        self.view['entry_name'].set_text(self.model.state.name)

    def change_description(self, textview, otherwidget):
        tbuffer = textview.get_buffer()
        entry_text = tbuffer.get_text(tbuffer.get_start_iter(), tbuffer.get_end_iter())
        print "State %s changed description from '%s' to: '%s'\n" % (self.model.state.state_id,
                                                                     self.model.state.description, entry_text)
        self.model.state.description = entry_text
        self.view['description_textview'].get_buffer().set_text(self.model.state.description)
