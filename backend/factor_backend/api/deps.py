from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from factor_backend.config import get_settings

_bearer = HTTPBearer(auto_error=False)


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """生产鉴权：Authorization: Bearer <API_TOKEN>"""
    settings = get_settings()
    if settings.auth_disabled:
        return "auth-disabled"

    expected = (settings.api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务未配置 API_TOKEN，且 AUTH_DISABLED=false",
        )
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer Token")
    if creds.credentials != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")
    return creds.credentials


AuthDep = Depends(require_api_token)
