from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


async def send_sftp(
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    key_filename: str | None = None,
    remote_path: str = "/",
    file_data: bytes | None = None,
    filename: str | None = None,
) -> bool:
    """Send a file via SFTP.

    Args:
        host: SFTP server hostname
        port: SFTP server port
        username: SFTP username
        password: Optional SFTP password
        key_filename: Optional SSH key filename
        remote_path: Remote directory path
        file_data: File data bytes
        filename: Remote filename

    Returns:
        True if successful, False otherwise
    """
    try:
        import asyncssh

        connect_kwargs = {}
        if password:
            connect_kwargs["password"] = password
        if key_filename:
            connect_kwargs["client_keys"] = [key_filename]

        async with asyncssh.connect(host, port=port, username=username, **connect_kwargs) as conn, conn.start_sftp_client() as sftp:
            remote_file = f"{remote_path.rstrip('/')}/{filename}" if filename else remote_path
            # put_file needs a binary stream (or local path), not raw bytes.
            await sftp.put_file(io.BytesIO(file_data), remote_file)

        logger.info(f"SFTP file sent to {host}:{remote_path}/{filename}")
        return True

    except Exception as e:
        logger.error(f"Failed to send SFTP file: {e}")
        return False


async def test_connection(
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    key_filename: str | None = None,
) -> tuple[bool, str]:
    """Verify SFTP connectivity and authentication without transferring any files.

    Returns a (success, message) tuple.
    """
    if not host or not username:
        return False, "Host and username are required"

    try:
        import asyncssh

        connect_kwargs = {}
        if password:
            connect_kwargs["password"] = password
        if key_filename:
            connect_kwargs["client_keys"] = [key_filename]

        async with asyncssh.connect(host, port=port, username=username, **connect_kwargs):
            logger.info(f"SFTP connection test succeeded for {host}:{port}")
            return True, "Connected and authenticated successfully"

    except ImportError:
        return False, "asyncssh is not installed"
    except Exception as e:
        logger.error(f"SFTP connection test failed for {host}:{port}: {e}")
        return False, f"Connection failed: {e}"
