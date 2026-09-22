from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import RetrieveAPIView

from apps.accounts.models import User
from apps.accounts.serializers import MeSerializer


@extend_schema_view(get=extend_schema(operation_id="me_retrieve", responses=MeSerializer))
class MeView(RetrieveAPIView[User]):
    serializer_class = MeSerializer

    def get_object(self) -> User:
        user = self.request.user
        assert isinstance(user, User)
        return user
