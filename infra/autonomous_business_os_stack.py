from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_auth
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as cloudfront_origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subscriptions
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class AutonomousBusinessOsStack(Stack):
    """Foundational, private document-ingest resources for the dev environment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        monthly_budget_usd: float = 20,
        budget_alert_email: str | None = None,
        invoice_sale_vendor: str = "Example Supplier",
        invoice_sale_invoice_number: str = "P-05",
        business_name: str = "",
        business_gstin: str = "",
        business_address: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if monthly_budget_usd <= 0:
            raise ValueError("monthly_budget_usd must be greater than zero")

        Tags.of(self).add("Project", "autonomous-business-os")
        Tags.of(self).add("Environment", environment_name)

        budget_alert_topic = sns.Topic(
            self,
            "BudgetAlertTopic",
            topic_name=f"autonomous-business-os-{environment_name}-budget-alerts",
            enforce_ssl=True,
        )
        budget_alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetsToPublish",
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[budget_alert_topic.topic_arn],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{self.partition}:budgets::{self.account}:*"
                    },
                },
            )
        )
        if budget_alert_email:
            budget_alert_topic.add_subscription(
                sns_subscriptions.EmailSubscription(budget_alert_email)
            )
        operations_alarm_topic = sns.Topic(
            self,
            "OperationsAlarmTopic",
            topic_name=f"autonomous-business-os-{environment_name}-operations-alerts",
            enforce_ssl=True,
        )
        if budget_alert_email:
            operations_alarm_topic.add_subscription(
                sns_subscriptions.EmailSubscription(budget_alert_email)
            )
        operations_alarm_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchAlarmsToPublish",
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[operations_alarm_topic.topic_arn],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{self.partition}:cloudwatch:{self.region}:{self.account}:alarm:*"
                    },
                },
            )
        )

        user_pool = cognito.UserPool(
            self,
            "ApplicationUserPool",
            user_pool_name=f"autonomous-business-os-{environment_name}",
            feature_plan=cognito.FeaturePlan.LITE,
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(email=cognito.StandardAttribute(required=True, mutable=True)),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        domain_prefix = f"abos-{environment_name}-{self.account}"
        hosted_domain = user_pool.add_domain(
            "HostedLoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )
        frontend_bucket = s3.Bucket(
            self,
            "FrontendAssetsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        frontend_distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
            # The generated *.cloudfront.net endpoint uses CloudFront's default
            # certificate, which fixes the minimum protocol at TLSv1. A TLS 1.2
            # minimum requires an alternate domain and matching certificate.
            enable_ipv6=True,
        )
        frontend_dist_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        s3deploy.BucketDeployment(
            self,
            "PublishFrontendAssets",
            sources=[s3deploy.Source.asset(str(frontend_dist_dir))],
            destination_bucket=frontend_bucket,
            distribution=frontend_distribution,
            distribution_paths=["/*"],
            prune=True,
        )
        frontend_url = f"https://{frontend_distribution.domain_name}"
        oauth_callbacks = [
            "http://localhost:5173/auth/callback",
            "http://127.0.0.1:5173/auth/callback",
            f"{frontend_url}/auth/callback",
        ]
        oauth_logout_urls = ["http://localhost:5173", "http://127.0.0.1:5173", frontend_url]
        user_pool_client = user_pool.add_client(
            "BrowserApplicationClient",
            user_pool_client_name=f"autonomous-business-os-{environment_name}-browser",
            generate_secret=False,
            prevent_user_existence_errors=True,
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=oauth_callbacks,
                logout_urls=oauth_logout_urls,
            ),
        )

        def _budget_notification(notification_type: str, threshold: int):
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    comparison_operator="GREATER_THAN",
                    notification_type=notification_type,
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[
                    budgets.CfnBudget.SubscriberProperty(
                        address=budget_alert_topic.topic_arn,
                        subscription_type="SNS",
                    )
                ],
            )

        budgets.CfnBudget(
            self,
            "MonthlyAccountCostBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=monthly_budget_usd,
                    unit="USD",
                ),
                budget_name=f"autonomous-business-os-{environment_name}-monthly-account-cost",
                budget_type="COST",
                time_unit="MONTHLY",
            ),
            notifications_with_subscribers=[
                _budget_notification("ACTUAL", 80),
                _budget_notification("ACTUAL", 100),
                _budget_notification("FORECASTED", 100),
            ],
        )

        dead_letter_queue = sqs.Queue(
            self,
            "InvoiceProcessingDeadLetterQueue",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        invoice_queue = sqs.Queue(
            self,
            "InvoiceProcessingQueue",
            visibility_timeout=Duration.minutes(6),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=dead_letter_queue,
                max_receive_count=3,
            ),
            enforce_ssl=True,
        )

        invoice_bucket = s3.Bucket(
            self,
            "InvoiceDocumentsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )

        completion_topic = sns.Topic(
            self,
            "TextractCompletionTopic",
            topic_name=f"AmazonTextract-{environment_name}-invoice-complete",
            enforce_ssl=True,
        )
        completion_dlq = sqs.Queue(
            self,
            "TextractCompletionDeadLetterQueue",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        completion_queue = sqs.Queue(
            self,
            "TextractCompletionQueue",
            visibility_timeout=Duration.minutes(31),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(queue=completion_dlq, max_receive_count=3),
            enforce_ssl=True,
        )
        completion_topic.add_subscription(sns_subscriptions.SqsSubscription(completion_queue))

        job_table = dynamodb.Table(
            self,
            "InvoiceAnalysisJobs",
            partition_key=dynamodb.Attribute(
                name="idempotency_key",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=False
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        business_data_table = dynamodb.Table(
            self,
            "BusinessData",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=False
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        business_data_table.add_global_secondary_index(
            index_name="gsi1",
            partition_key=dynamodb.Attribute(name="gsi1pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi1sk", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        lambda_package_dir = Path(__file__).resolve().parents[1] / "backend" / "build" / "lambda"
        if not lambda_package_dir.is_dir():
            raise FileNotFoundError(
                "Backend Lambda package is missing. Run backend/package_lambda.ps1 before CDK synthesis."
            )
        lambda_code = lambda_.Code.from_asset(str(lambda_package_dir))
        api_log_group = logs.LogGroup(
            self,
            "BusinessApiLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        common_api_environment = {
            "APP_ENV": environment_name,
            "BUSINESS_DATA_TABLE_NAME": business_data_table.table_name,
            "INVOICE_BUCKET_NAME": invoice_bucket.bucket_name,
            "EXTRACTION_JOBS_TABLE_NAME": job_table.table_name,
            "INVOICE_SALE_VENDOR": invoice_sale_vendor,
            "INVOICE_SALE_INVOICE_NUMBER": invoice_sale_invoice_number,
            "BUSINESS_NAME": business_name,
            "BUSINESS_GSTIN": business_gstin,
            "BUSINESS_ADDRESS": business_address,
            "AWS_EXTRACTION_ENABLED": "true",
            "MAX_UPLOAD_BYTES": str(8 * 1024 * 1024),
            "CORS_ALLOWED_ORIGINS": f"http://localhost:5173,http://127.0.0.1:5173,{frontend_url}",
        }
        api_function = lambda_.Function(
            self,
            "BusinessApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda_handler.handler",
            code=lambda_code,
            timeout=Duration.seconds(30),
            memory_size=1024,
            environment=common_api_environment,
            log_group=api_log_group,
        )
        business_data_table.grant_read_write_data(api_function)
        authorizer = apigwv2_auth.HttpJwtAuthorizer(
            "CognitoJwtAuthorizer",
            f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[user_pool_client.user_pool_client_id],
        )
        http_api = apigwv2.HttpApi(
            self,
            "BusinessHttpApi",
            api_name=f"autonomous-business-os-{environment_name}",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", frontend_url],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["Authorization", "Content-Type"],
                max_age=Duration.hours(1),
            ),
        )
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "BusinessApiIntegration", api_function
            ),
            authorizer=authorizer,
        )
        # Browser CORS preflight requests do not carry the Cognito bearer token.
        # Give OPTIONS its own unauthenticated route so it is selected ahead of
        # the protected ANY route; FastAPI's CORSMiddleware returns the preflight
        # response using the exact origins configured above.
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigwv2.HttpMethod.OPTIONS],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "BusinessApiOptionsIntegration", api_function
            ),
        )

        textract_service_role = iam.Role(
            self,
            "TextractNotificationRole",
            assumed_by=iam.ServicePrincipal(
                "textract.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{self.partition}:textract:*:{self.account}:*"
                    },
                },
            ),
        )
        textract_service_role.add_to_policy(
            iam.PolicyStatement(actions=["sns:Publish"], resources=[completion_topic.topic_arn])
        )
        backend_lambda_dir = Path(__file__).resolve().parents[1] / "backend" / "lambda"
        starter_log_group = logs.LogGroup(
            self,
            "InvoiceStarterLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        start_worker = lambda_.Function(
            self,
            "StartInvoiceAnalysis",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="start_expense.handler",
            code=lambda_.Code.from_asset(str(backend_lambda_dir / "start_expense")),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "JOB_TABLE_NAME": job_table.table_name,
                "TEXTRACT_TOPIC_ARN": completion_topic.topic_arn,
                "TEXTRACT_ROLE_ARN": textract_service_role.role_arn,
            },
            log_group=starter_log_group,
        )
        invoice_bucket.grant_read_write(api_function, "incoming/*")
        invoice_bucket.grant_read(api_function, "processed/*")
        job_table.grant_read_data(api_function)
        start_worker.add_event_source(event_sources.SqsEventSource(invoice_queue, batch_size=1))
        invoice_bucket.grant_read(start_worker, "incoming/*")
        job_table.grant_read_write_data(start_worker)
        start_worker.add_to_role_policy(
            iam.PolicyStatement(actions=["textract:StartExpenseAnalysis"], resources=["*"])
        )
        start_worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[textract_service_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "textract.amazonaws.com"}},
            )
        )

        completion_log_group = logs.LogGroup(
            self,
            "InvoiceCompletionLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        completion_worker = lambda_.Function(
            self,
            "CompleteInvoiceAnalysis",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="complete_expense.handler",
            code=lambda_.Code.from_asset(str(backend_lambda_dir / "complete_expense")),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "JOB_TABLE_NAME": job_table.table_name,
                "INVOICE_BUCKET_NAME": invoice_bucket.bucket_name,
                "TEXTRACT_TOPIC_ARN": completion_topic.topic_arn,
                "TEXTRACT_ROLE_ARN": textract_service_role.role_arn,
            },
            log_group=completion_log_group,
        )
        completion_worker.add_event_source(
            event_sources.SqsEventSource(completion_queue, batch_size=1)
        )
        job_table.grant_read_write_data(completion_worker)
        invoice_bucket.grant_read_write(completion_worker, "processed/*")
        completion_worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:GetExpenseAnalysis",
                    "textract:StartDocumentAnalysis",
                    "textract:GetDocumentAnalysis",
                ],
                resources=["*"],
            )
        )
        completion_worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[textract_service_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "textract.amazonaws.com"}},
            )
        )

        alarm_defaults = {
            "evaluation_periods": 1,
            "treat_missing_data": cloudwatch.TreatMissingData.NOT_BREACHING,
            "actions_enabled": True,
        }
        alarm_targets = [
            ("BusinessApiErrorsAlarm", "Business API Lambda errors", api_function.metric_errors(period=Duration.minutes(5)), 0),
            ("InvoiceStarterErrorsAlarm", "Invoice start-worker errors", start_worker.metric_errors(period=Duration.minutes(5)), 0),
            ("InvoiceCompletionErrorsAlarm", "Invoice completion-worker errors", completion_worker.metric_errors(period=Duration.minutes(5)), 0),
            ("InvoiceQueueAgeAlarm", "Invoice queue oldest-message age", invoice_queue.metric_approximate_age_of_oldest_message(period=Duration.minutes(5)), 300),
            ("CompletionQueueAgeAlarm", "Textract completion queue oldest-message age", completion_queue.metric_approximate_age_of_oldest_message(period=Duration.minutes(5)), 600),
            ("InvoiceDeadLetterAlarm", "Invoice processing dead-letter messages", dead_letter_queue.metric_approximate_number_of_messages_visible(period=Duration.minutes(5)), 0),
            ("CompletionDeadLetterAlarm", "Textract completion dead-letter messages", completion_dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(5)), 0),
        ]
        for alarm_id, alarm_name, metric, threshold in alarm_targets:
            alarm = cloudwatch.Alarm(
                self,
                alarm_id,
                alarm_name=f"{environment_name}-{alarm_name.lower().replace(' ', '-')}",
                metric=metric,
                threshold=threshold,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                **alarm_defaults,
            )
            alarm.add_alarm_action(cloudwatch_actions.SnsAction(operations_alarm_topic))

        api_5xx_metric = cloudwatch.Metric(
            namespace="AWS/ApiGateway",
            metric_name="5xx",
            dimensions_map={"ApiId": http_api.api_id, "Stage": "$default"},
            statistic="sum",
            period=Duration.minutes(5),
        )
        api_5xx_alarm = cloudwatch.Alarm(
            self,
            "HttpApi5xxAlarm",
            alarm_name=f"{environment_name}-http-api-5xx",
            metric=api_5xx_metric,
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            **alarm_defaults,
        )
        api_5xx_alarm.add_alarm_action(cloudwatch_actions.SnsAction(operations_alarm_topic))

        # Process documents uploaded by the application under this prefix only.
        for suffix in (".pdf", ".jpeg", ".jpg", ".png"):
            invoice_bucket.add_event_notification(
                s3.EventType.OBJECT_CREATED,
                s3n.SqsDestination(invoice_queue),
                s3.NotificationKeyFilter(prefix="incoming/", suffix=suffix),
            )

        CfnOutput(self, "InvoiceBucketName", value=invoice_bucket.bucket_name)
        CfnOutput(self, "InvoiceQueueUrl", value=invoice_queue.queue_url)
        CfnOutput(self, "InvoiceDeadLetterQueueUrl", value=dead_letter_queue.queue_url)
        CfnOutput(self, "TextractCompletionQueueUrl", value=completion_queue.queue_url)
        CfnOutput(self, "InvoiceAnalysisJobTableName", value=job_table.table_name)
        CfnOutput(self, "BudgetAlertTopicArn", value=budget_alert_topic.topic_arn)
        CfnOutput(self, "OperationsAlarmTopicArn", value=operations_alarm_topic.topic_arn)
        CfnOutput(self, "MonthlyAccountBudgetUsd", value=str(monthly_budget_usd))
        CfnOutput(self, "BusinessApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "FrontendUrl", value=frontend_url)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(self, "FrontendDistributionId", value=frontend_distribution.distribution_id)
        CfnOutput(self, "BusinessDataTableName", value=business_data_table.table_name)
        CfnOutput(self, "CognitoUserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "CognitoAppClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(
            self,
            "CognitoHostedUiBaseUrl",
            value=f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com",
        )
