"""Candidate detail: one application as the company sees it.

A few endpoints also serve the recruiter who submitted the candidate.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.chat.api import MESSAGE_PAGE_PARAMETERS, message_page
from apps.chat.serializers import MessagePageSerializer, MessageSerializer
from apps.core.documents import extract_text
from apps.core.http import content_disposition
from apps.hiring.access import (
    DETAIL_HINTS,
    company_application_or_404,
    shared_application_or_404,
    teammates_who_can_view,
)
from apps.hiring.cv import CvKind, cv_kind
from apps.hiring.discussion import post_message
from apps.hiring.models import ApprovalRequest, ApprovalStatus, CandidateNote, Feedback, Interview
from apps.hiring.serializers import (
    NOT_YOUR_NOTE,
    ONLY_APPROVER,
    PICK_A_TEAMMATE,
    ApplicationDetailSerializer,
    ApprovalCreateSerializer,
    ApprovalDecisionSerializer,
    ArchiveSerializer,
    CandidateNoteSerializer,
    CvTextSerializer,
    DiscussionMessageCreateSerializer,
    FeedbackSerializer,
    FeedbackWriteSerializer,
    InterviewCreateSerializer,
    NoteCreateSerializer,
    TeammateSerializer,
    WaitingApprovalSerializer,
)
from apps.hiring.workflow import (
    STAGE_MOVED,
    StageConflict,
    archive,
    decide_approval,
    make_offer,
    mark_hired,
    move_forward,
    request_approval,
    schedule_interview,
)
from apps.jobs.access import can_view_job


def acting_user(request: Request) -> User:
    return cast(User, request.user)


def detail_response(
    request: Request, application: Application, *, code: int = status.HTTP_200_OK
) -> Response:
    fresh = (
        Application.objects.select_related(*DETAIL_HINTS)
        .prefetch_related("interviews")
        .get(pk=application.pk)
    )
    return Response(
        ApplicationDetailSerializer(fresh, context={"request": request}).data, status=code
    )


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"], operation_id="applications_retrieve", responses=ApplicationDetailSerializer
    )
)
class ApplicationDetailView(APIView):
    def get(self, request: Request, pk: Any) -> Response:
        return detail_response(request, company_application_or_404(acting_user(request), pk))


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = STAGE_MOVED
    default_code = "stage_conflict"


@contextmanager
def conflicts_as_409() -> Iterator[None]:
    try:
        yield
    except StageConflict as error:
        raise Conflict(str(error)) from error


class StageCommandView(APIView):
    """POST runs one stage command and answers with the fresh detail."""

    def run(self, request: Request, application: Application) -> None:
        raise NotImplementedError

    def post(self, request: Request, pk: Any) -> Response:
        application = company_application_or_404(acting_user(request), pk)
        with conflicts_as_409():
            self.run(request, application)
        return detail_response(request, application)


def command_schema(operation_id: str, body: Any = None) -> Any:
    return extend_schema_view(
        post=extend_schema(
            tags=["hiring"],
            operation_id=operation_id,
            request=body,
            responses=ApplicationDetailSerializer,
        )
    )


@command_schema("applications_move_forward_create")
class MoveForwardView(StageCommandView):
    def run(self, request: Request, application: Application) -> None:
        move_forward(application)


@command_schema("applications_offer_create")
class OfferView(StageCommandView):
    def run(self, request: Request, application: Application) -> None:
        make_offer(application)


@command_schema("applications_hire_create")
class HireView(StageCommandView):
    def run(self, request: Request, application: Application) -> None:
        mark_hired(application)


@command_schema("applications_archive_create", ArchiveSerializer)
class ArchiveView(StageCommandView):
    def run(self, request: Request, application: Application) -> None:
        payload = ArchiveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        archive(application, payload.validated_data["reason"])


@extend_schema_view(
    post=extend_schema(
        tags=["hiring"],
        operation_id="applications_interviews_create",
        request=InterviewCreateSerializer,
        responses={201: ApplicationDetailSerializer},
    )
)
class InterviewCreateView(APIView):
    def post(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        application = company_application_or_404(user, pk)
        payload = InterviewCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        with conflicts_as_409():
            schedule_interview(application, created_by=user, **payload.validated_data)
        return detail_response(request, application, code=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="applications_approvers_list",
        responses=TeammateSerializer(many=True),
    )
)
class ApproversView(APIView):
    def get(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        application = company_application_or_404(user, pk)
        teammates = teammates_who_can_view(application, excluding=user)
        return Response(TeammateSerializer(teammates, many=True).data)


@extend_schema_view(
    post=extend_schema(
        tags=["hiring"],
        operation_id="applications_approval_requests_create",
        request=ApprovalCreateSerializer,
        responses={201: ApplicationDetailSerializer},
    )
)
class ApprovalRequestCreateView(APIView):
    def post(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        application = company_application_or_404(user, pk)
        payload = ApprovalCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        wanted = payload.validated_data["approver_id"]
        approver = next(
            (
                member
                for member in teammates_who_can_view(application, excluding=user)
                if member.pk == wanted
            ),
            None,
        )
        if approver is None:
            raise ValidationError({"approver_id": [PICK_A_TEAMMATE]})
        with conflicts_as_409():
            request_approval(
                application,
                requested_by=user,
                approver=approver,
                note=payload.validated_data["note"],
            )
        return detail_response(request, application, code=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        tags=["hiring"],
        operation_id="approval_requests_decision_create",
        request=ApprovalDecisionSerializer,
        responses=ApplicationDetailSerializer,
    )
)
class ApprovalDecisionView(APIView):
    def post(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        approval = get_object_or_404(
            ApprovalRequest.objects.select_related("application__job", "approver"), pk=pk
        )
        if not can_view_job(user, approval.application.job):
            raise Http404
        if approval.approver_id != user.pk:
            raise PermissionDenied(ONLY_APPROVER.format(name=approval.approver.full_name))
        payload = ApprovalDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        with conflicts_as_409():
            decide_approval(
                approval,
                approve=payload.validated_data["approve"],
                note=payload.validated_data["note"],
            )
        return detail_response(request, approval.application)


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="approval_requests_waiting_list",
        responses=WaitingApprovalSerializer(many=True),
    )
)
class WaitingApprovalsView(APIView):
    """Requests waiting for the caller's decision, oldest first (the Home box)."""

    def get(self, request: Request) -> Response:
        user = acting_user(request)
        waiting = (
            ApprovalRequest.objects.filter(approver=user, status=ApprovalStatus.PENDING)
            .select_related("application__job", "application__candidate", "requested_by")
            .order_by("created_at")
        )
        visible = [approval for approval in waiting if can_view_job(user, approval.application.job)]
        return Response(WaitingApprovalSerializer(visible, many=True).data)


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="applications_notes_list",
        responses=CandidateNoteSerializer(many=True),
    ),
    post=extend_schema(
        tags=["hiring"],
        operation_id="applications_notes_create",
        request=NoteCreateSerializer,
        responses={201: CandidateNoteSerializer},
    ),
)
class NotesView(APIView):
    def get(self, request: Request, pk: Any) -> Response:
        application = company_application_or_404(acting_user(request), pk)
        notes = application.company_notes.select_related("author")
        return Response(
            CandidateNoteSerializer(notes, many=True, context={"request": request}).data
        )

    def post(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        application = company_application_or_404(user, pk)
        payload = NoteCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        note = CandidateNote.objects.create(
            application=application, author=user, body=payload.validated_data["body"]
        )
        return Response(
            CandidateNoteSerializer(note, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    delete=extend_schema(tags=["hiring"], operation_id="notes_destroy", responses={204: None})
)
class NoteDetailView(APIView):
    def delete(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        note = get_object_or_404(CandidateNote.objects.select_related("application__job"), pk=pk)
        if not can_view_job(user, note.application.job):
            raise Http404
        if note.author_id != user.pk:
            raise PermissionDenied(NOT_YOUR_NOTE)
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="applications_feedback_list",
        responses=FeedbackSerializer(many=True),
    )
)
class FeedbackListView(APIView):
    def get(self, request: Request, pk: Any) -> Response:
        application = company_application_or_404(acting_user(request), pk)
        rows = (
            Feedback.objects.filter(interview__application=application)
            .select_related("interview", "author")
            .order_by("interview__round", "created_at")
        )
        return Response(FeedbackSerializer(rows, many=True, context={"request": request}).data)


@extend_schema_view(
    put=extend_schema(
        tags=["hiring"],
        operation_id="interviews_feedback_update",
        request=FeedbackWriteSerializer,
        responses=FeedbackSerializer,
    )
)
class InterviewFeedbackView(APIView):
    """Each teammate keeps one verdict per round; saving again replaces it."""

    def put(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        interview = get_object_or_404(Interview.objects.select_related("application__job"), pk=pk)
        if not can_view_job(user, interview.application.job):
            raise Http404
        payload = FeedbackWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        feedback, _ = Feedback.objects.update_or_create(
            interview=interview, author=user, defaults=payload.validated_data
        )
        return Response(FeedbackSerializer(feedback, context={"request": request}).data)


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="applications_messages_list",
        parameters=MESSAGE_PAGE_PARAMETERS,
        responses=MessagePageSerializer,
    ),
    post=extend_schema(
        tags=["hiring"],
        operation_id="applications_messages_create",
        request=DiscussionMessageCreateSerializer,
        responses={201: MessageSerializer},
    ),
)
class DiscussionView(APIView):
    """Messages about this candidate in the (job, recruiter) channel; both sides can post."""

    def get(self, request: Request, pk: Any) -> Response:
        application = shared_application_or_404(acting_user(request), pk)
        return Response(message_page(request, application.messages.all()))

    def post(self, request: Request, pk: Any) -> Response:
        user = acting_user(request)
        application = shared_application_or_404(user, pk)
        payload = DiscussionMessageCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        message = post_message(application, author=user, body=payload.validated_data["body"])
        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"],
        operation_id="applications_cv_inline_retrieve",
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
)
class CvInlineView(APIView):
    """The submitted PDF for the in-page viewer; other formats have no inline view."""

    def get(self, request: Request, pk: Any) -> FileResponse:
        application = shared_application_or_404(acting_user(request), pk)
        if cv_kind(application.cv_filename) != CvKind.PDF or not application.cv_file:
            raise Http404
        try:
            handle = application.cv_file.open("rb")
        except (FileNotFoundError, OSError) as error:
            raise Http404 from error
        response = FileResponse(handle, content_type="application/pdf")
        response["Content-Disposition"] = content_disposition(application.cv_filename, inline=True)
        response["X-Content-Type-Options"] = "nosniff"
        # Opened straight in a tab, an uploaded PDF must not run with this origin's rights.
        response["Content-Security-Policy"] = "sandbox"
        return response


@extend_schema_view(
    get=extend_schema(
        tags=["hiring"], operation_id="applications_cv_text_retrieve", responses=CvTextSerializer
    )
)
class CvTextView(APIView):
    """The CV's text layer: how a DOCX is shown. Empty for DOC files and scans."""

    def get(self, request: Request, pk: Any) -> Response:
        application = shared_application_or_404(acting_user(request), pk)
        if not application.cv_file:
            raise Http404
        try:
            with application.cv_file.open("rb") as handle:
                extracted = extract_text(handle)
        except (FileNotFoundError, OSError) as error:
            raise Http404 from error
        return Response(CvTextSerializer({"text": extracted}).data)
