from __future__ import annotations

import typing

from app.server.api.v1.endpoints import workshop as workshop_endpoints


def test_connectors_list_request_type_is_resolvable() -> None:
    """POST /connectors/list 的 body 注解必须可解析，不得 NameError"""
    hints = typing.get_type_hints(workshop_endpoints.list_connectors)
    assert "body" in hints
    assert hints["body"].__name__ == "WorkshopConnectorListRequest"
