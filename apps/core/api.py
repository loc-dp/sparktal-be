from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthSerializer(serializers.Serializer[dict[str, str]]):
    status = serializers.CharField()


class HealthView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="health_retrieve", responses=HealthSerializer)
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class CsrfView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="csrf_retrieve", request=None, responses={204: None})
    @method_decorator(ensure_csrf_cookie)
    def get(self, request: Request) -> Response:
        return Response(status=status.HTTP_204_NO_CONTENT)
