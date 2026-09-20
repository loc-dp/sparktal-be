from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import RetrieveAPIView

from apps.accounts.models import User
from apps.accounts.serializers import MeSerializer


# On a generic view the decorator has to target the method, not the class:
# drf-spectacular rejects a class-level operation_id ("will most likely result in
# a broken schema").
@extend_schema_view(get=extend_schema(operation_id="me_retrieve", responses=MeSerializer))
class MeView(RetrieveAPIView[User]):
    serializer_class = MeSerializer

    def get_object(self) -> User:
        user = self.request.user
        assert isinstance(user, User)
        return user
