class ServiceError(Exception):
    """A business-rule refusal with a user-presentable message."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.message = message
        self.errors = list(errors or [])
