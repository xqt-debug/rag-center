class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def payload(self):
        return {"code": self.code, "message": self.message}


def require(condition, code, message, status=400):
    if not condition:
        raise DomainError(code, message, status)
