"""Read a CV with Claude. Output is raw; pass it through normalize_fields()."""

from __future__ import annotations

import anthropic
from anthropic.types import OutputConfigParam
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel

from apps.companies.models import location_codes
from apps.core.documents import ClaudeRead, anthropic_client, read_with_claude, source_block

__all__ = ["ClaudeRead", "ParsedCv", "instructions", "llm_extract"]

MAX_OUTPUT_TOKENS = 4000
OUTPUT_CONFIG: OutputConfigParam = {"effort": "medium"}


class JobPeriod(BaseModel):
    start: str | None
    end: str | None


class ParsedCv(BaseModel):
    is_cv: bool
    full_name: str | None
    current_title: str | None
    email: str | None
    phone: str | None
    dob: str | None
    location: str | None
    is_expat: bool | None
    languages: list[str]
    skills: list[str]
    jobs: list[JobPeriod]
    years_experience: int | None
    current_salary: int | None
    expected_salary: int | None


def instructions() -> str:
    codes = location_codes(with_other=True)
    return (
        "Extract the candidate's details from this CV. It may be in Vietnamese, English or both.\n"
        f"Today is {timezone.localdate().isoformat()}.\n"
        "Rules:\n"
        "- Use null (or an empty list) for anything the CV does not state. Never guess.\n"
        "- is_cv: false if the file is not a CV or résumé (e.g. a report, a certificate, a job "
        "description, a letter); then leave everything else empty.\n"
        "- full_name: the candidate's full name exactly as written. Keep every Vietnamese "
        "diacritic the CV shows (Đào, not Dao) but never add diacritics the CV does not show "
        "('Vu Tien Hung' stays as it is). Leave out nicknames and English aliases however they "
        "are attached ('(Vicky)', '- STEVEN'), honorifics and degrees ('Ph.D.', 'MBA'). If the "
        "name is only in Chinese, Korean or another non-Latin script, give the Latin spelling "
        "the CV prints for it (beside it, in the email, in publications); if it prints none, "
        "transliterate it (pinyin for Chinese). Never give an English alias on its own: when one "
        "stands beside the full Vietnamese name ('SALLY NGUYEN (NGUYEN THI XANH)'), give the "
        "Vietnamese name. Write the name in its usual order without a comma ('Hung, Vu Tien' "
        "is 'Vu Tien Hung').\n"
        "- current_title: the title of the latest paid job, by its dates and not by the order "
        "on the page: the current one, else the one that ended last. Paid jobs are the ones "
        "jobs below counts, so an internship, a thesis or other student research never beats "
        "one, even when it is newer or current: 'Sales Staff' 2023-2025 beats 'Marketing "
        "Intern' 2025-present. Only if the CV has no paid job, give the latest internship's "
        "title; null if it lists no work at all. Never take the title from the headline under "
        "the name, an objective or the position applied for, even when the only work is an "
        "internship. Give just the title, without the employer, the department or a part-time "
        "note. Keep it in the language the CV writes it in, never translating it; when the CV "
        "gives it in both Vietnamese and English ('Kế toán trưởng (Chief Accountant)'), give "
        "the Vietnamese. Give a title written in Chinese, Korean or Japanese in English. Write "
        "it in normal case even when the CV writes it in capitals: an English title in title "
        "case ('SALES MANAGER' gives 'Sales Manager'), a Vietnamese one in sentence case "
        "('NHÂN VIÊN KINH DOANH' and 'Nhân Viên Kinh Doanh' give 'Nhân viên kinh doanh'); "
        "acronyms such as QA or SEO stay.\n"
        "- email, phone: the candidate's own, never a referee's or an employer's; only one each "
        "(the first if several), copied exactly as written in the CV. If the phone has no "
        "country code and the candidate lives outside Vietnam, add that country's code "
        "(+886 for Taiwan).\n"
        "- dob: date of birth as YYYY-MM-DD; null if only an age or a year is given.\n"
        f"- location: where the candidate lives, from a home address or a stated current city, "
        f"as one of these codes: {codes}. The codes are provinces after the July 2025 merger, "
        "so an old province merged into one gets its code. Use 'other' for any other "
        "Vietnamese province the CV names, and 'abroad' if they live outside Vietnam, "
        "whatever their nationality: a foreigner living in Saigon (越南西貢) is 'hcm'. Null "
        "if not stated or if only 'Vietnam' is given. Never infer it from where they study "
        "or work: a school or employer address alone gives null.\n"
        "- is_expat: true only if the candidate is clearly a foreign national: a stated "
        "non-Vietnamese nationality, or a non-Vietnamese name with a home and career abroad; "
        "else null.\n"
        "- languages: the spoken languages the CV names or certifies, in English, e.g. "
        "['Vietnamese', 'English']; a test gives its language (TOEIC or IELTS gives English, "
        "JLPT Japanese, HSK Chinese); 'Chinese' for Mandarin. Do not add any other.\n"
        "- skills: up to 20 professional skills, most important first, each a short name of at "
        "most 40 characters.\n"
        "- jobs: every paid job in the work history. Leave out education, internships, student "
        "research and compulsory military or police service; a paid trainee programme after "
        "graduating and Japan's technical intern programme (thực tập sinh kỹ năng) are jobs. "
        "start and end as written: 'YYYY-MM' when the month is given, 'YYYY' when only the "
        "year is; end is 'present' only when the CV marks the job as current ('Present', "
        "'Now', 'Hiện tại'), null when it is blank or not a date. A single date ('2024') is "
        "both start and end. Jobs listed under one shared date range each get that range. "
        "Copy the dates only; do not add them up.\n"
        "- years_experience: only a total the CV states outright ('8 years of experience'); "
        "'N+', 'over N' and 'hơn N năm' give N, a range gives its lower number; else null.\n"
        "- current_salary, expected_salary: monthly gross in VND as a whole number "
        "('25 triệu' = 25000000; a range gives its lower number); null if not stated or not "
        "in VND."
    )


def _client() -> anthropic.Anthropic:
    return anthropic_client(settings.ANTHROPIC_API_KEY, settings.CV_PARSE_TIMEOUT_SECONDS)


def llm_extract(data: bytes, filename: str, text: str) -> ClaudeRead | None:
    """Return Claude's raw fields and cost, or None when no reply came back at all."""
    if not (settings.ANTHROPIC_API_KEY and settings.CV_PARSE_MODEL):
        return None
    source = source_block(data, filename, text, "cv")
    if source is None:
        return None
    return read_with_claude(
        _client(),
        model=settings.CV_PARSE_MODEL,
        source=source,
        instructions=instructions(),
        output_format=ParsedCv,
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config=OUTPUT_CONFIG,
        label="CV",
    )
