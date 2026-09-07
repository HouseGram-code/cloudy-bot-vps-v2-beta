class CloudyError(Exception):
    """Only these deliberately sanitized messages may be sent to Discord."""
    def __init__(self, message: str, code: str = "operation_failed"):
        super().__init__(message)
        self.code = code
