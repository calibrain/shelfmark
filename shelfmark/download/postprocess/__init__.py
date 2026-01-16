"""Post-download processing pipeline.

This package contains the post-download processing pipeline (staging, scanning,
archive extraction, transfers, and safe cleanup) and the router that selects an
output handler.

Output handlers live in `shelfmark.download.outputs` and should depend on
`pipeline` (not `router`) to avoid circular imports.
"""

from .router import post_process_download
