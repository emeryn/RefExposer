"""The version the application reports.

It is **stamped into the image at build time** from the git tag, rather than kept in this file, so a release is
a tag and nothing else: there is no second place to remember to edit, and no way for the image called 1.2.0 to
answer 1.0.0.

`REFEX_BUILD_VERSION` is set by the runtime stage of backend/Dockerfile, which takes it from the build
argument the publish workflow passes (`docker/metadata-action`'s version, which is the tag without its `v`).
A source checkout, or a build that passes nothing, says so plainly instead of claiming a release it is not.

The name is **not** `REFEX_VERSION`: that one belongs to the host, where it picks which image tag to run, and
the backend reads `.env` into its own environment — so sharing the name would let `REFEX_VERSION=latest` in a
deployment file make the application report its version as "latest".
"""

import os

__version__ = os.environ.get("REFEX_BUILD_VERSION") or "0.0.0+dev"
