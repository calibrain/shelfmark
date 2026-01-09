"""Core module - shared models, queue, and utilities."""

from shelfmark.core.models import BookInfo, QueueItem, SearchFilters, QueueStatus
from shelfmark.core.queue import BookQueue, book_queue
from shelfmark.core.logger import setup_logger
