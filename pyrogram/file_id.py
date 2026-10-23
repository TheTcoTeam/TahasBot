"""Stand-in for pyrogram.file_id. Bale file ids are opaque strings."""


class FileId:
    def __init__(self, file_id: str = "", **kw):
        self.file_id = file_id
        for k, v in kw.items():
            setattr(self, k, v)

    @classmethod
    def decode(cls, file_id: str):
        return cls(file_id)

    def encode(self) -> str:
        return self.file_id
