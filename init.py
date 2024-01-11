import boto3
import json
from botocore.config import Config
from datetime import datetime
import time
import os

my_config = Config(
    region_name = 'us-east-1',
)

aws_signer_client = boto3.client('signer', config=my_config)
iam_client = boto3.client('iam', config=my_config)
lambda_client = boto3.client('lambda', config=my_config)

def create_signing_profile(timestamp):
    response = aws_signer_client.put_signing_profile(
        profileName='MySigningProfile{0}'.format(timestamp),
        signatureValidityPeriod={
            'value': 7,
            'type': 'DAYS'
        },
        platformId='AWSLambda-SHA384-ECDSA',
        tags={}
    )
    print('successfully created signing profile')
    return response

def attach_to_role(role_name, policy_arn):
    response = iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn
    )
    print('successfully attached policy to IAM role')

def create_policy(name, description):
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": [
                    "arn:aws:logs:*:*:*"
                ]
            }
        ]
    }

    create_policy_response = iam_client.create_policy(
            PolicyName=name,
            Description=description,
            PolicyDocument=json.dumps(policy_doc),
        )
    
    print('successfully created IAM policy')
    return create_policy_response

# create IAM role for lambda function
def create_role(timestamp):
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": [
                        "lambda.amazonaws.com",
                        "edgelambda.amazonaws.com"
                    ]
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }

    create_role_response = iam_client.create_role(Path='/service-role/',RoleName='MyOriginRequestFunctionRole{0}'.format(timestamp), AssumeRolePolicyDocument=json.dumps(trust_policy))
    create_policy_response = create_policy('MyOriginRequestFunctionRolePolicy{0}'.format(timestamp), 'An IAM policy to granting permissions for Lambda function MyOriginRequestFunction')
    attach_to_role('MyOriginRequestFunctionRole{0}'.format(timestamp), create_policy_response.get('Policy').get('Arn'))
    time.sleep(15)  
    print('succesfully created IAM role')
    return create_role_response

# create Lambda function
def create_lambda_function(timestamp):

    # create IAM role for function here
    iam_role_arn = create_role(timestamp).get('Role').get('Arn')
    
    with open('function.zip', 'rb') as f:
        zipped_code = f.read()

    create_function_response = lambda_client.create_function(
        FunctionName='MyOriginRequestFunction{0}'.format(timestamp),
        Runtime='nodejs20.x',
        Role=iam_role_arn,
        Handler='index.js',
        Code=dict(ZipFile=zipped_code),
        Description='string',
        Timeout=10,
        MemorySize=128,
        Publish=True,
        PackageType='Zip',
        Architectures=[
            'x86_64',
        ],
        EphemeralStorage={
            'Size': 512
        }
    )

    print('successfully created lambda function')
    return create_function_response.get('FunctionArn')

def create_signing_configuration(profile_version_arn):
    # signing profile has an ARN and profile version ARN
    # ensure that you specify the profile version ARN and not the just the profile ARN
    response = lambda_client.create_code_signing_config(
        Description='A signing configuration for Lambda',
        AllowedPublishers={
            'SigningProfileVersionArns': [
                profile_version_arn,
            ]
        },
        CodeSigningPolicies={
            'UntrustedArtifactOnDeployment': 'Enforce'
        }
    )

    return response.get('CodeSigningConfig').get('CodeSigningConfigArn')


if __name__ == "__main__":

    # current date and time
    current_time = datetime.now()
    timestamp = str(datetime.timestamp(current_time)).split('.')[0]

    # create the signing profile
    create_signing_profile_response = create_signing_profile(timestamp)
    signing_profile_arn = create_signing_profile_response.get('arn')
    signing_profile_version_arn = create_signing_profile_response.get('profileVersionArn')

    # create lambda function
    function_arn = create_lambda_function(timestamp)

    # create signing configuration
    signing_configuration_arn = create_signing_configuration(signing_profile_version_arn)

    # update lambda function to use signing configuration
    response = lambda_client.put_function_code_signing_config(
        CodeSigningConfigArn=signing_configuration_arn,
        FunctionName=function_arn
    )

    # create an S3 bucket that will be used during the signing job and for function code
    s3_client = boto3.client('s3', config=my_config)
    create_s3_bucket_response = s3_client.create_bucket(
        Bucket='my-test-bucket-{0}'.format(timestamp),
        ObjectOwnership='BucketOwnerPreferred'
    )

    response = s3_client.put_bucket_versioning(
        Bucket='my-test-bucket-{0}'.format(timestamp),
        VersioningConfiguration={
            'MFADelete': 'Disabled',
            'Status': 'Enabled'
        },
    )

    important_values = {
        "SIGNING_PROFILE_ARN": signing_profile_arn,
        "SIGNING_PROFILE_VERSION_ARN": signing_profile_version_arn,
        "SIGNING_CONFIGURATION_ARN": signing_configuration_arn,
        "FUNCTION_ARN": function_arn,
        "S3_BUCKET": create_s3_bucket_response.get('Location').split('/')[1]
    }

    f = open(".env", "a")
    for item in important_values:
        f.write('{0}={1}\n'.format(item, important_values[item]))
    f.close()