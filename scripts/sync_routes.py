import argparse
import json
import os
from typing import List, Optional

import boto3


def find_rest_api_id_by_name(client, name: str) -> Optional[str]:
    paginator = client.get_paginator("get_rest_apis")
    for page in paginator.paginate(limit=500):
        for item in page.get("items", []):
            if item.get("name") == name:
                return item.get("id")
    return None


def ensure_path(client, rest_api_id: str, path: str) -> str:
    resources = {}
    paginator = client.get_paginator("get_resources")
    for page in paginator.paginate(restApiId=rest_api_id, limit=500):
        for res in page.get("items", []):
            resources[res.get("path")] = res.get("id")

    if path in resources:
        return resources[path]

    segments = [seg for seg in path.split("/") if seg]
    parent_id = resources.get("/")
    built_path = ""
    for seg in segments:
        built_path = f"{built_path}/{seg}" if built_path else f"/{seg}"
        if built_path in resources:
            parent_id = resources[built_path]
            continue
        created = client.create_resource(restApiId=rest_api_id, parentId=parent_id, pathPart=seg)
        parent_id = created["id"]
        resources[built_path] = parent_id

    return resources[path]


def ensure_method_and_integration(client, rest_api_id: str, resource_id: str, http_method: str, lambda_arn: str):
    http_method = http_method.upper()
    try:
        client.get_method(restApiId=rest_api_id, resourceId=resource_id, httpMethod=http_method)
    except client.exceptions.NotFoundException:
        client.put_method(
            restApiId=rest_api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            authorizationType="NONE",
        )

    uri = f"arn:aws:apigateway:${{AWS_REGION}}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    sts = boto3.client("sts")
    region = boto3.session.Session().region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        region = sts.meta.region_name or "us-east-1"
    uri = uri.replace("${AWS_REGION}", region)

    try:
        client.get_integration(restApiId=rest_api_id, resourceId=resource_id, httpMethod=http_method)
        client.put_integration(
            restApiId=rest_api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=uri,
        )
    except client.exceptions.NotFoundException:
        client.put_integration(
            restApiId=rest_api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=uri,
        )


def add_permission_for_apigw(lambda_client, lambda_arn: str, rest_api_id: str, account_id: str, region: str):
    function_name = lambda_arn.split(":function:")[-1]
    sid = f"apigw-{rest_api_id}-{function_name}"
    source_arn = f"arn:aws:execute-api:{region}:{account_id}:{rest_api_id}/*/*/*"
    try:
        lambda_client.add_permission(
            FunctionName=lambda_arn,
            StatementId=sid,
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass


def deploy_stage(client, rest_api_id: str, stage: str):
    client.create_deployment(restApiId=rest_api_id, stageName=stage)


def load_paths(config_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("config.json must be a JSON array of path strings")
        return [str(p) for p in data]


def main():
    parser = argparse.ArgumentParser(description="Sync API Gateway routes with module config.json")
    parser.add_argument("--module", required=True, help="Module name, e.g., users or orders")
    parser.add_argument("--stage", default=os.environ.get("STAGE", "prod"))
    parser.add_argument("--api-name", default="MainApiGateway")
    parser.add_argument("--lambda-name-prefix", default="project-")
    parser.add_argument("--modules-dir", default=os.path.join(os.path.dirname(__file__), "..", "modules"))
    args = parser.parse_args()

    modules_dir = os.path.abspath(args.modules_dir)
    config_path = os.path.join(modules_dir, args.module, "config.json")
    lambda_name = f"{args.lambda_name_prefix}{args.module}"

    apigw = boto3.client("apigateway")
    lambda_client = boto3.client("lambda")
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    region = (
        boto3.session.Session().region_name
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    print(f"[sync] module={args.module} stage={args.stage} api_name={args.api_name}")
    print(f"[sync] modules_dir={modules_dir} config_path={config_path}")
    print(f"[sync] account_id={account_id} region={region}")

    rest_api_id = find_rest_api_id_by_name(apigw, args.api_name)
    if not rest_api_id:
        raise RuntimeError(
            f"REST API with name {args.api_name} not found. Ensure SAM created it: {args.api_name}"
        )
    print(f"[sync] rest_api_id={rest_api_id}")

    print(f"[sync] resolving lambda name={lambda_name}")
    lambda_fn = lambda_client.get_function(FunctionName=lambda_name)
    lambda_arn = lambda_fn["Configuration"]["FunctionArn"]
    print(f"[sync] lambda_arn={lambda_arn}")

    paths = load_paths(config_path)
    print(f"[sync] desired_paths={paths}")
    for path in paths:
        print(f"[sync] ensuring path={path}")
        resource_id = ensure_path(apigw, rest_api_id, path)
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            print(f"[sync] ensuring method={method} on resource_id={resource_id}")
            ensure_method_and_integration(apigw, rest_api_id, resource_id, method, lambda_arn)

    add_permission_for_apigw(lambda_client, lambda_arn, rest_api_id, account_id, region)
    print("[sync] deployed permissions; creating deployment")
    deploy_stage(apigw, rest_api_id, args.stage)
    print(f"[sync] complete: {len(paths)} paths synced for module={args.module} on API={args.api_name} stage={args.stage}")


if __name__ == "__main__":
    main()


