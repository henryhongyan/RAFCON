
from utils import log
logger = log.get_logger(__name__)

from gtkmvc import Controller
from gtk import ListStore


class TransitionListController(Controller):
    """Controller handling the view of transitions of the ContainerStateModel

    This :class:`gtkmvc.Controller` class is the interface between the GTK widget view
    :class:`mvc.views.transitions.TransitionListView` and the transitions of the
    :class:`mvc.models.state.ContainerStateModel`. Changes made in
    the GUI are written back to the model and vice versa.

    :param mvc.models.ContainerStateModel model: The container state model containing the data
    :param mvc.views.TransitionListView view: The GTK view showing the transitions as a table
    """

    def __init__(self, model, view):
        """Constructor
        """
        Controller.__init__(self, model, view)


    def register_view(self, view):
        """Called when the View was registered
        """

        def cell_text(column, cell_renderer, model, iter, container_model):
            col = column.get_name()
            states_dic = {-1: 'Parent'}
            states_store = ListStore(str, str)
            states_store.append([container_model.container_state.state_id, container_model.container_state.name])
            transition = model.get_value(iter, 0)
            for state_model in container_model.states:
                states_dic[state_model.state.state_id] = state_model.state.name
                states_store.append([state_model.state.state_id, state_model.state.name])
            if col == 'from_state_col':
                cell_renderer.set_property('text', states_dic[transition.from_state])
                cell_renderer.set_property('text-column', 1)
                cell_renderer.set_property('model', states_store)
            elif col == 'to_state_col':
                cell_renderer.set_property('text', states_dic[transition.to_state])
                cell_renderer.set_property('text-column', 1)
                cell_renderer.set_property('model', states_store)
            elif col == 'from_outcome_col':
                cell_renderer.set_property('text', transition.from_outcome)
            elif col == 'to_outcome_col':
                cell_renderer.set_property('text', transition.to_outcome)
            else:
                logger.error("Unknown column '{col:s}' in TransitionListView".format(col=col))


        view.get_top_widget().set_model(self.model.transition_list_store)

        view['from_state_col'].set_cell_data_func(view['from_state_combo'], cell_text, self.model)
        view['to_state_col'].set_cell_data_func(view['to_state_combo'], cell_text, self.model)
        view['from_outcome_col'].set_cell_data_func(view['from_outcome_combo'], cell_text, self.model)
        view['to_outcome_col'].set_cell_data_func(view['to_outcome_combo'], cell_text, self.model)


        view['from_state_combo'].connect("edited", self.on_combo_changed)
        view['to_state_combo'].connect("edited", self.on_combo_changed)

    def register_adapters(self):
        """Adapters should be registered in this method call
        """


    def on_combo_changed(self, widget, path, text):
        logger.debug("Widget: {widget:s} - Path: {path:s} - Text: {text:s}".format(widget=widget, path=path, text=text))

