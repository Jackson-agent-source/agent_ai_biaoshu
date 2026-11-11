from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.agent.services.layout_service import LayoutRequestSerializer, create_layout_plan
from apps.common.responses import success_response


class ArticleLayoutView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = 'agent_generate'

    def post(self, request):
        serializer = LayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return success_response(create_layout_plan(serializer.validated_data['blocks'], user=request.user))
