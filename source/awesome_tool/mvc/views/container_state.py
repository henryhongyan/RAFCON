
from gtkmvc import View

from views.transition_list import TransitionListView
from views.data_flow_list import DataFlowListView

class ContainerStateView(View):

    builder = './glade/ContainerStateWidget.glade'
    top = 'container_state_widget'

    def __init__(self):
        View.__init__(self)

        self.transition_list_view = TransitionListView()
        self.data_flow_list_view = DataFlowListView()

        self['transition_scroller'].add(self.transition_list_view.get_top_widget())
        self['data_flow_scroller'].add(self.data_flow_list_view.get_top_widget())

    pass