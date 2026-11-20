import logging

__version__ = "0.7.0"

# stderr carries the error and hint lines of a command; log records must not join them.
logging.getLogger(__name__).addHandler(logging.NullHandler())
