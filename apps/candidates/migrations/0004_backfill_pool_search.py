import hashlib
import re
import unicodedata

import phonenumbers
from django.contrib.postgres.search import SearchVector
from django.db import migrations
from django.db.models import Value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def backfill(apps, schema_editor) -> None:
    candidate_model = apps.get_model("candidates", "Candidate")
    database = schema_editor.connection.alias
    for candidate in candidate_model.objects.using(database).iterator(chunk_size=500):
        email = (candidate.email or "").strip().lower()
        phone = ""
        if candidate.phone:
            try:
                parsed = phonenumbers.parse(str(candidate.phone), "VN")
                if phonenumbers.is_possible_number(parsed):
                    phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException:
                pass
        name = (candidate.full_name or "").replace("đ", "d").replace("Đ", "D")
        name = "".join(
            char for char in unicodedata.normalize("NFKD", name) if not unicodedata.combining(char)
        )
        name = re.sub(r"\s+", " ", name).strip().lower()
        name_dob = f"{name}|{candidate.dob.isoformat()}" if name and candidate.dob else ""
        text = " ".join(
            part
            for part in (
                candidate.full_name,
                candidate.current_title,
                " ".join(candidate.skills),
                candidate.notes,
            )
            if part
        )
        candidate_model.objects.using(database).filter(pk=candidate.pk).update(
            email_hash=digest(email),
            phone_hash=digest(phone),
            name_dob_hash=digest(name_dob),
            search_vector=SearchVector(Value(text), config="simple"),
        )


class Migration(migrations.Migration):
    dependencies = [("candidates", "0003_cvparse")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
