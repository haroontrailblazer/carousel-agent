"""Keep every app Storage operation beneath users/<authenticated UUID>/."""
from app import tenancy


class TenantStorageClient:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def key(key):
        owner = tenancy.require()
        key = str(key)
        if key.startswith("/") or "\\" in key or any(p in (".", "..") for p in key.split("/")):
            raise ValueError("Invalid media path")
        if key.startswith("users/"):
            raise PermissionError("Use a workspace-relative media path")
        return f"users/{owner}/{key}"

    def put_object(self, **kwargs):
        return self.client.put_object(**(kwargs | {"Key": self.key(kwargs["Key"])}))

    def get_object(self, **kwargs):
        return self.client.get_object(**(kwargs | {"Key": self.key(kwargs["Key"])}))

    def head_object(self, **kwargs):
        return self.client.head_object(**(kwargs | {"Key": self.key(kwargs["Key"])}))

    def delete_object(self, **kwargs):
        return self.client.delete_object(**(kwargs | {"Key": self.key(kwargs["Key"])}))

    def delete_objects(self, **kwargs):
        deletion = kwargs["Delete"]
        result = self.client.delete_objects(**(kwargs | {"Delete": deletion | {
            "Objects": [obj | {"Key": self.key(obj["Key"])} for obj in deletion["Objects"]]
        }}))
        prefix = f"users/{tenancy.require()}/"
        return result | {"Deleted": [obj | {"Key": obj["Key"][len(prefix):]} for obj in result.get("Deleted", []) if obj["Key"].startswith(prefix)]}

    def generate_presigned_url(self, operation, *, Params, **kwargs):
        return self.client.generate_presigned_url(operation, Params=Params | {"Key": self.key(Params["Key"])}, **kwargs)

    def get_paginator(self, operation):
        delegate = self.client.get_paginator(operation)
        class Paginator:
            def paginate(inner, **kwargs):
                prefix = f"users/{tenancy.require()}/"
                for page in delegate.paginate(**(kwargs | {"Prefix": self.key(kwargs.get("Prefix", ""))} )):
                    yield page | {"Contents": [obj | {"Key": obj["Key"][len(prefix):]}
                        for obj in page.get("Contents", []) if obj["Key"].startswith(prefix)]}
        return Paginator()

    def download_file(self, bucket, key, filename):
        return self.client.download_file(bucket, self.key(key), filename)

    def upload_file(self, filename, bucket, key, **kwargs):
        return self.client.upload_file(filename, bucket, self.key(key), **kwargs)

    def close(self):
        closer = getattr(self.client, "close", None)
        if closer: closer()
