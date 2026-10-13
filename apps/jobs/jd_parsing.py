"""Read a job description with Claude. Output is raw; pass it through normalize_jd()."""

from __future__ import annotations

from typing import Literal

import anthropic
from anthropic.types import OutputConfigParam
from django.conf import settings
from pydantic import BaseModel

from apps.companies.models import location_codes
from apps.core.documents import ClaudeRead, anthropic_client, read_with_claude, source_block

# Long JDs list 30+ bullets copied word for word; 8000 leaves room for that.
MAX_OUTPUT_TOKENS = 8000
OUTPUT_CONFIG: OutputConfigParam = {"effort": "low"}


class Salary(BaseModel):
    minimum: int | None
    maximum: int | None
    currency: str | None
    period: Literal["hour", "day", "week", "month", "year"] | None


class ParsedJd(BaseModel):
    is_job_description: bool
    title: str | None
    roles: list[str]
    qualifications: list[str]
    nice_to_have: list[str]
    benefits: list[str]
    salary: Salary | None
    pay_basis: Literal["gross", "net"] | None
    work_model: Literal["onsite", "hybrid", "remote"] | None
    # The place as written, so a city outside the codes can still be named to the company.
    location: str | None
    city: str | None
    skills: list[str]
    # Last, so a draft is written after the duties it is drawn from have been read out.
    why_we_need_you_drafted: bool
    why_we_need_you: str | None


def instructions() -> str:
    codes = location_codes(with_other=False)
    return (
        "You fill in the 'Create new job' form of a hiring marketplace in Vietnam from the job "
        "description (JD) above. The hiring company checks your result and posts it; freelance "
        "recruiters then read it to find candidates. A dropped duty or requirement, a wrong "
        "salary or a wrong place costs them a hire, so be complete and exact.\n"
        "The JD may be in Vietnamese, English or both. It may be a company form (often tables: "
        "'Bảng mô tả công việc' or 'MTCV' with label | value rows, KPI tables, sign-off "
        "blocks), an agency posting or a copy from a job board. PDF and Word layouts split "
        "sentences across lines, break words, jumble columns and repeat headers.\n"
        "\n"
        "General rules:\n"
        "- Copy text verbatim, in the JD's own language and wording. Never translate, "
        "summarise, reword or fix grammar. Use null (or an empty list) for anything the JD "
        "does not state. Never guess. The one exception is why_we_need_you, below; the title "
        "also gets a light clean-up.\n"
        "- Repairing the layout is not rewording: join a sentence the layout split across "
        "lines or bullets (a line that starts in lowercase or with 'and', 'or', 'và' usually "
        "continues the one before), rejoin broken words ('cutting -edge', 'high-' + "
        "'performance'), and drop bullet characters, numbering and a trailing ';' or ','.\n"
        "- A label with a short value is one item with its label kept, so it still makes "
        "sense alone: 'Hỗ trợ suất ăn: 01 bữa/ngày', 'Thái độ: Trung thực, Hợp tác, Học hỏi', "
        "'Knowledge: Employee relations & Government relations'.\n"
        "- A heading that only names a group ('Quản lý nhân sự:', 'Technical', 'Nhiệm vụ "
        "chính') is not an item. A line that introduces sub-items ('..., including:', '... như "
        "sau:') is joined with them when they are short fragments ('Define HR strategy, "
        "including: hiring; retention; payroll'). When the sub-items are full sentences, each "
        "is its own item, and the intro line stays only if it is a duty in itself.\n"
        "- Two languages: when the JD gives the same content in Vietnamese and English (side "
        "by side, line by line, or as two whole versions), use only the language that comes "
        "first, in every field but title. Never output an item and its translation as two "
        "items.\n"
        "- Leave out document metadata (form codes, revision dates, page numbers, approval "
        "and signature blocks), recruiter or agency contact details, how-to-apply "
        "instructions, and any part that is clearly about a different job.\n"
        "\n"
        "Fields:\n"
        "- is_job_description: false if the file is not a job description (e.g. a CV, an "
        "invoice); then leave everything else empty.\n"
        "- title: the job title as the JD names it; an explicit 'Job title', 'Chức danh' or "
        "'Vị trí' field beats a heading. The title only: remove the company, client or business "
        "unit name, location, salary, headcount, contract type or length, schedule, urgency "
        "('Urgent', 'Tuyển gấp'), recruiting words ('Tuyển dụng', 'Hiring') and candidate "
        "qualifiers, also when they sit in brackets ('Sales Manager (ABC Group, HCM)' is "
        "'Sales Manager'; drop '(Kinh nghiệm chứng khoán)'). Keep seniority and "
        "specialisation ('Senior Backend Engineer', not 'URGENT - Senior Backend Engineer - "
        "HCM - Up to $3000'). If "
        "the JD names the job in two languages, write 'Vietnamese title (English title)'; "
        "never repeat an abbreviation beside its full form ('QC / Quality Control Manager' is "
        "'Quality Control Manager'). Write an ALL-CAPS title in normal case with acronyms (QA, "
        "HR, IT, R&D) kept, and fix obvious spelling mistakes in it ('SUSTAINABILTY "
        "DIRECTOR' is 'Sustainability Director', 'Assuarance' is 'Assurance').\n"
        "- roles: every responsibility or duty, one item per bullet or sentence as written, "
        "in the JD's order. Look beyond the responsibilities heading: duties also sit in "
        "daily, weekly or monthly task lists, deliverables, accountabilities and the goals of "
        "a KPI table (leave out KPI measures, targets, weights and percentages, and goals "
        "that repeat a duty already listed). If the JD says whom the job reports to or which "
        "team it manages and no item says so, add that as the last item, as written "
        "('Báo cáo cho: Giám đốc nhà máy', 'Reports to: Country HR Head').\n"
        "- qualifications: every must-have requirement wherever it appears in the JD "
        "(experience, education, skills, languages, certifications, nationality, travel, "
        "health, traits such as 'We value people who...'), one item per bullet, verbatim.\n"
        "- nice_to_have: only requirements the JD marks as optional or preferred in words or "
        "by a heading ('a plus', 'preferred', 'nice to have', 'bonus', 'ưu tiên', 'là lợi "
        "thế', 'là một điểm cộng'), never from the layout alone. Everything under a heading "
        "such as 'Preferred qualifications', 'Nice to have' or 'Ưu tiên' belongs here, "
        "including its sub-headings ('Tools requirements:'), until the next main heading. "
        "Move them out of qualifications; never list an item twice.\n"
        "- Every requirement bullet in the JD ends up in exactly one of qualifications or "
        "nice_to_have; never drop one. A preference that only narrows a must-have ('5 years "
        "in B2B sales, preferably in payments') stays whole in qualifications. A preference "
        "that reads as a full requirement of its own ('Tốt nghiệp Đại học trở lên; ưu tiên "
        "ứng viên am hiểu thị trường TMĐT Châu Âu') is split off into nice_to_have, and the "
        "must-have part stays in qualifications.\n"
        "- benefits: everything the job offers besides the salary figure, one item per "
        "bullet, verbatim: perks, allowances, bonuses, 13th-month pay, insurance, leave, "
        "training, career growth, equipment, the working environment and 'why join us' "
        "points. Also the working arrangement wherever the JD states it: working hours and "
        "days, shifts, overtime, contract type and length, probation, start date ('Thời gian "
        "làm việc: 08:00 - 17:00, Thứ 2 - Thứ 7', 'Start date: ASAP'). Leave out the line that "
        "gives the salary used below and lines that only say pay is competitive or "
        "negotiable; keep a line about bonus, commission or total income on top of it.\n"
        "- salary: the base pay as written, with amounts as whole numbers in full units "
        "('15 - 20 triệu' = 15000000 and 20000000; '$1,500' = 1500; '25M' = 25000000; "
        "'22.000.000' = 22000000). 'Up to X' or 'tối đa X' fills only maximum; 'from X' or "
        "'từ X trở lên' only minimum; a single figure fills both. A stated figure counts even "
        "beside 'negotiable' ('Lương thỏa thuận từ 22.000.000 trở lên' = minimum 22000000). "
        "When the JD gives a base salary and a larger total income (base plus bonus, "
        "commission or allowances), use the base salary. currency as an ISO code (VND, USD, "
        "...). period is the unit the amount is paid per; null if the JD does not say. "
        "salary is null when pay is only 'negotiable', 'competitive', 'thỏa thuận' or not "
        "given.\n"
        "- pay_basis: 'gross' ('Gross', 'trước thuế') or 'net' ('Net', 'sau thuế', 'thực "
        "nhận', or the company pays the personal income tax), only when the JD says so; else "
        "null.\n"
        "- work_model: only when the JD states it: 'remote' (fully remote / làm việc từ xa), "
        "'hybrid' (part office, part home), 'onsite' (explicitly full-time at the office). "
        "An office address alone is not a work model: null.\n"
        "- location: the city or province where the job is done, as the JD names it ('Thủ Đức, "
        "TP.HCM', 'Vũng Tàu', 'Hải Dương'): place names only, no street address or notes, and "
        "no country unless the job is outside Vietnam ('Tokyo, Japan', 'Singapore'). "
        "A stated work location ('Nơi làm việc', 'Địa điểm làm việc', 'Location') or a "
        "place named beside the job title ('Sales Manager - HCM', 'Base in Vietnam (Anlao)') "
        "beats any address, which is often the head office. Name every place if there are "
        "several ('Hà Nội, TP.HCM'). Null if the JD names none or only Vietnam.\n"
        f"- city: the code for location: {codes}. The codes are provinces after the July 2025 "
        "merger, so a place gets the same code whether the JD names its old or new province. "
        "A district, ward, town or industrial park counts as its province (Thủ Đức, Dĩ An, "
        "Vũng Tàu = hcm; Hội An = dn; Biên Hòa = dnai; An Lão = hp; Từ Sơn = bn). Several "
        "sites in one city are that city. 'abroad' for a job outside Vietnam. Null if the job is "
        "in several cities or in a place with no code.\n"
        "- skills: up to 20 skills a recruiter would search candidates by, most important "
        "first: the core discipline or domain of the job ('Điện lạnh công nghiệp', 'Physical "
        "design', 'Garment manufacturing'), then methods, standards, tools, technologies and "
        "certifications, then the foreign languages the JD requires ('English', 'Tiếng Trung'; "
        "not Vietnamese). Not degrees or years of experience. Each "
        "a short name ('Go', 'IFRS', 'Figma', 'ISO 22000'), not a sentence, in the JD's "
        "wording and language, using the name people search for ('Cadence', not 'CDNS'). "
        "Must-have skills before nice-to-have ones. Leave out generic soft skills "
        "(communication, teamwork, leadership, problem solving, giao tiếp, làm việc nhóm), "
        "bare business terms (KPI, ROI, CAPEX), the company's internal system names, and "
        "separate office apps ('MS Office' covers Word, Excel and PowerPoint unless the JD "
        "asks for advanced use of one). Alternatives stay one entry ('ACCA/CMA/CPA'); a list "
        "of brands takes at most two entries.\n"
        "- why_we_need_you: it opens the job card, so its first sentence must say why this "
        "role exists. Take the JD's own paragraph on what this role is for, whatever its "
        "heading ('About the role', 'Job purpose', 'Job scope', 'Job introduction', 'Objectives', "
        "'Tóm lược', 'Mục tiêu', 'Mục đích công việc', 'Tổng quan vị trí', or an opening 'We "
        "are seeking...' / 'Chúng tôi đang tìm' paragraph even under a wrong heading such as "
        "'Company profile'). Copy it whole and exactly, adding nothing, with "
        "why_we_need_you_drafted false. Not the company's 'About us', and not a paragraph left "
        "over from another posting that names a different job ('We are recruiting an "
        "accountant' in a JD titled 'Sales Engineer'). "
        "If there is "
        "no such paragraph, you must write one: 1-3 sentences in the JD's language, using "
        "only facts and wording found in the JD. Lead with the business context the duties "
        "alone do not give (the product or market, the team, its size or goal, whom the role "
        "reports to), then what this hire will own; do not restate the first duty. Then set "
        "why_we_need_you_drafted true. Never mix the two: the JD's paragraph exactly "
        "(false) or your own text (true). Leave it null only when the JD lists no duties at "
        "all."
    )


def _client() -> anthropic.Anthropic:
    return anthropic_client(settings.ANTHROPIC_API_KEY, settings.JD_PARSE_TIMEOUT_SECONDS)


def is_enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY and settings.JD_PARSE_MODEL)


def llm_extract(data: bytes, filename: str, text: str) -> ClaudeRead | None:
    """Return Claude's raw fields and cost, or None when no reply came back at all."""
    if not is_enabled():
        return None
    source = source_block(data, filename, text, "jd")
    if source is None:
        return None
    return read_with_claude(
        _client(),
        model=settings.JD_PARSE_MODEL,
        source=source,
        instructions=instructions(),
        output_format=ParsedJd,
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config=OUTPUT_CONFIG,
        label="JD",
    )
