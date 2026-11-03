import json
import shlex


def generate_curl_command(
    method: str,
    url: str,
    headers: dict | None = None,
    data: dict | None = None
) -> str:
    """
    Generate a curl command from request data.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        url: Full URL of the request
        headers: Request headers dictionary
        data: Request body data dictionary

    Returns:
        String containing the curl command
    """
    # Start with curl and method
    curl = f"curl -X {method}"

    # Add headers
    if headers:
        for key, value in headers.items():
            # Skip common headers that are automatically added
            if key.lower() in ['content-length', 'cookie', 'set-cookie']:
                continue
            curl += f" -H {shlex.quote(f'{key}: {value}')}"

    # Add data/body
    if data:
        json_data = json.dumps(data)
        curl += f" -d {shlex.quote(json_data)}"

    # Add URL
    curl += f" {shlex.quote(url)}"

    return curl
