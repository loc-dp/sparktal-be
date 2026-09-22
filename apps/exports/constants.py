from datetime import timedelta

# A ZIP bigger than this is a sign someone wants the whole pipeline, not an export.
MAX_EXPORT_FILES = 200
# The download link dies after a day; the purge task then removes the blob.
EXPORT_TTL = timedelta(hours=24)
# Building a ZIP reads every CV out of storage, so cap how often one user may ask.
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 600
