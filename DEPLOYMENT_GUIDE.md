# Complete AWS Lambda CI/CD Pipeline Guide

This guide teaches you how to build any serverless API project that auto-deploys to AWS using:
- AWS SAM (infrastructure + packaging)
- GitHub Actions (CI/CD with OIDC, no access keys)
- API Gateway (shared REST API)
- A sync script that auto-creates API routes per module

Use it as a blueprint for new projects (e.g., Timesheets).

---

## 1) What you will build
- Multiple Lambda modules under `modules/<module>/` (e.g., `timesheets`, `reports`).
- One shared API Gateway (e.g., `MainApiGateway`).
- CI/CD pipelines that deploy only the changed module(s).
- Route sync: for each module, `config.json` lists required paths; missing resources/methods are auto-created.

---

## 2) Prerequisites
- AWS account with permissions for IAM, CloudFormation, Lambda, API Gateway, S3
- GitHub repository
- Git installed locally

Optional (not required for CI):
- AWS CLI
- AWS SAM CLI

---

## 3) Repository layout (pattern)
```
modules/
  <moduleA>/
    app.py          # Lambda handler
    config.json     # ["/moduleA", "/moduleA/{id}"]
  <moduleB>/
    app.py
    config.json
scripts/
  sync_routes.py    # Auto-creates API resources/methods + Lambda proxy integration
.github/
  workflows/
    deploy-<moduleA>.yml
    deploy-<moduleB>.yml
template.yaml        # SAM template (shared API + functions)
```

---

## 4) SAM template (template.yaml)
Defines the shared API and each Lambda.

Example scaffold:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Project API with shared API Gateway

Globals:
  Function:
    Runtime: python3.9
    Timeout: 10
    MemorySize: 256

Parameters:
  StageName:
    Type: String
    Default: prod
    Description: API Gateway stage name

Resources:
  MainApi:
    Type: AWS::Serverless::Api
    Properties:
      Name: MainApiGateway
      StageName: !Ref StageName
      EndpointConfiguration: REGIONAL

  ExampleFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: project-example
      CodeUri: modules/example/
      Handler: app.lambda_handler
      Policies:
        - AWSLambdaBasicExecutionRole
      Events:
        Health:
          Type: Api
          Properties:
            RestApiId: !Ref MainApi
            Path: /_health/example
            Method: GET

Outputs:
  MainApiId:
    Description: Rest API ID
    Value: !Ref MainApi
  MainApiUrl:
    Description: Base invoke URL
    Value: !Sub https://${MainApi}.execute-api.${AWS::Region}.amazonaws.com/${StageName}/
```

---

## 5) Modules
- Code: `modules/<module>/app.py` (Python handler)
- Contract: `modules/<module>/config.json` lists desired API paths, e.g.:
```json
[
  "/timesheets",
  "/timesheets/{id}"
]
```

---

## 6) Route sync script (scripts/sync_routes.py)
Responsibilities:
- Find REST API by name (e.g., `MainApiGateway`).
- Ensure each path in `modules/<module>/config.json` exists (create intermediate resources as needed).
- Ensure methods (GET, POST, PUT, DELETE, PATCH, OPTIONS) exist, integrated to the Lambda with `AWS_PROXY`.
- Add Lambda permission for API Gateway to invoke the function.
- Deploy the API stage (e.g., `prod`).

---

## 7) GitHub Actions workflows
One workflow per module so only changed modules deploy.

Example: `.github/workflows/deploy-timesheets.yml`
```yaml
name: Deploy Timesheets Module

on:
  push:
    branches: [ main ]
    paths:
      - 'modules/timesheets/**'
      - 'template.yaml'
      - 'scripts/**'

permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      AWS_REGION: us-east-1
      STAGE: prod
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: aws-actions/setup-sam@v2
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - name: SAM Build
        run: sam build --use-container --debug
      - name: SAM Deploy
        run: |
          sam deploy \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --resolve-s3 \
            --stack-name project-shared \
            --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
            --parameter-overrides StageName=${{ env.STAGE }} \
            --debug
      - name: Install script deps
        run: |
          python -m pip install --upgrade pip
          python -m pip install boto3
      - name: Sync API routes
        run: python scripts/sync_routes.py --module timesheets --stage ${{ env.STAGE }}
```

---

## 8) One-time AWS setup (OIDC + IAM role)
1) IAM → Identity providers → Add provider
- Provider type: OpenID Connect
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

2) IAM → Roles → Create role
- Trusted entity: Web identity
- Identity provider: `token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`
- GitHub organization: YOUR_ORG_OR_USERNAME
- GitHub repository: YOUR_REPO_NAME
- GitHub branch: `main`
- Permissions: start with `AdministratorAccess` (tighten later)
- Name: `GithubActionsDeployRole`

3) GitHub → Repo → Settings → Secrets and variables → Actions → New secret
- Name: `AWS_DEPLOY_ROLE_ARN`
- Value: role ARN from the role Summary page

Trust policy JSON (manual edit, replace ACCOUNT_ID, ORG/REPO if needed):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:ORG/REPO:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

---

## 9) Deploy flow (on push)
- Workflow triggers when matching paths change.
- Assumes IAM role via OIDC using `AWS_DEPLOY_ROLE_ARN`.
- `sam build` + `sam deploy` apply `template.yaml` via CloudFormation.
- `sync_routes.py` ensures resources/methods/integrations exist.
- Stage is deployed; endpoints live at `MainApiUrl` stack output.

---

## 10) Verify deployment
- CloudFormation → stack `project-shared` → Outputs → `MainApiUrl`.
- API Gateway → REST APIs → confirm API and routes.
- Lambda → confirm `project-<module>` functions exist/updated.
- Test quickly:
  - `curl "$MainApiUrl/_health/<module>"`
  - `curl "$MainApiUrl/<module>"`
  - `curl "$MainApiUrl/<module>/123"`

---

## 11) Add a new module later
1) Create `modules/<new>/app.py` and `modules/<new>/config.json`.
2) Add a function block in `template.yaml` pointing to `modules/<new>/`.
3) Add `.github/workflows/deploy-<new>.yml` watching `modules/<new>/**`.
4) Push → pipeline deploys the new Lambda and auto-creates routes.

---

## 12) Troubleshooting
- Credentials step fails (OIDC/AssumeRole): trust policy must match `repo:ORG/REPO:ref:refs/heads/main` and secret must contain the role ARN.
- SAM deploy AccessDenied: role needs CFN, S3, Lambda, APIGW, plus minimal IAM (Create/Attach/Put/Delete RolePolicy, PassRole).
- API not found in sync: stack didn’t create the API; check CloudFormation Events.
- Malformed `config.json`: must be a valid JSON array.
- Workflow paths/working directory: must match repo layout.

---

## 13) Hardening (after it works)
- Narrow trust to specific branches/tags.
- Replace `AdministratorAccess` with least-privilege.
- Restrict `iam:PassRole` to only roles used by the stack.

---

## 14) Quickstart checklist
1) Create repo; add `modules/<module>/app.py` and `modules/<module>/config.json`.
2) Write `template.yaml` with shared API + function.
3) Add `scripts/sync_routes.py`.
4) Add `.github/workflows/deploy-<module>.yml`.
5) Configure OIDC provider and IAM role; set repo secret `AWS_DEPLOY_ROLE_ARN`.
6) Push a change → watch Actions → verify in CloudFormation, API Gateway, and Lambda.
