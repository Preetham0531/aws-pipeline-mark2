import json


def lambda_handler(event, context):
    path = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    if path.startswith("/_health/orders"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "ok", "service": "orders"}),
        }

    if path.startswith("/orders") and method in ("GET", "POST", "PUT", "DELETE"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": f"orders handler for {method} {path}"}),
        }

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }

import json


def lambda_handler(event, context):
    response_body = {
        "module": "orders",
        "message": "Orders Lambda is healthy",
        "path": event.get("resource"),
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }


