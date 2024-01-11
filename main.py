import boto3
import subprocess
import time
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

SIGNING_PROFILE_ARN = os.getenv('SIGNING_PROFILE_ARN')
SIGNING_PROFILE_VERSION_ARN = os.getenv('SIGNING_PROFILE_VERSION_ARN')
SIGNING_CONFIGURATION_ARN = os.getenv('SIGNING_CONFIGURATION_ARN')
FUNCTION_ARN = os.getenv('FUNCTION_ARN')
S3_BUCKET = os.getenv('S3_BUCKET')

from botocore.config import Config
my_config = Config(
    region_name = 'us-east-1',
)

def deploy_function_version():
    # zip contents of lambda_function_code
    subprocess.run(["zip", "-r", "function.zip", "lambda_function_code/"]) 

    s3_client = boto3.client('s3',config=my_config)
    
    print('Uploading function.zip to S3 bucket...')
    response = s3_client.upload_file('function.zip', S3_BUCKET, 'function.zip')
    print('Completed upload of function.zip to S3 bucket')

    head_object_response = s3_client.head_object(
        Bucket=S3_BUCKET,
        Key='function.zip'
    )

    # create signing job
    aws_signer_client = boto3.client('signer', config=my_config)
    signing_job_response = aws_signer_client.start_signing_job(
        source={
            's3': {
                'bucketName': S3_BUCKET,
                'key': 'function.zip',
                'version': head_object_response.get('VersionId')
            }
        },
        destination={
            's3': {
                'bucketName': S3_BUCKET,
                'prefix': 'signing_job_output/'
            }
        },
        profileName=SIGNING_PROFILE_ARN.split('/')[2]
    )

    waiter = aws_signer_client.get_waiter('successful_signing_job')
    waiter.wait(jobId=signing_job_response.get('jobId'))

    describe_job_response = aws_signer_client.describe_signing_job(
        jobId=signing_job_response.get('jobId')
    )

    time.sleep(5)

    # deploy function version using deployed code
    lambda_client = boto3.client('lambda', config=my_config)
    update_function_code_response = lambda_client.update_function_code(
        FunctionName=FUNCTION_ARN.split(':')[-1],
        S3Bucket=describe_job_response.get('signedObject').get('s3').get('bucketName'),
        S3Key=describe_job_response.get('signedObject').get('s3').get('key'),
        Publish=True
    )

    return update_function_code_response

    
def create_cloudfront_distribution(function_version_arn):
    # create a cloudfront distribution that uses the latest function version
    cloudfront_client = boto3.client('cloudfront')
    current_time = datetime.now()
    timestamp = str(datetime.timestamp(current_time)).split('.')[0]

    create_origin_access_control_response = cloudfront_client.create_origin_access_control(
        OriginAccessControlConfig={
            'Name': 'MyOriginAccessControl{0}'.format(timestamp),
            'Description': 'Origin access control for test CloudFrotn distribution',
            'SigningProtocol': 'sigv4',
            'SigningBehavior': 'always',
            'OriginAccessControlOriginType': 's3'
        }
    )
    time.sleep(5)
    
    origin_access_control_Id = create_origin_access_control_response.get('OriginAccessControl').get('Id')


    create_distribution_response = cloudfront_client.create_distribution(
        DistributionConfig={
            'CallerReference': timestamp,
            'DefaultRootObject': 'index.html',
            'Origins': {
                'Quantity': 1,
                'Items': [
                    {
                        'Id': 'mys3origin',
                        'DomainName': '{0}.s3.us-east-1.amazonaws.com'.format(S3_BUCKET),
                        'S3OriginConfig': {
                            'OriginAccessIdentity': ''  
                        },
                        'OriginAccessControlId': origin_access_control_Id
                    },
                ]
            },
            'DefaultCacheBehavior': {
                'TargetOriginId': 'mys3origin',
                'ViewerProtocolPolicy': 'redirect-to-https',
                'AllowedMethods': {
                    'Quantity': 3,
                    'Items': [
                        'GET','HEAD','OPTIONS',
                    ],
                    'CachedMethods': {
                        'Quantity': 3,
                        'Items': [
                            'GET','HEAD','OPTIONS',
                        ]
                    }
                },
                'Compress': True,
                'LambdaFunctionAssociations': {
                    'Quantity': 1,
                    'Items': [
                        {
                            'LambdaFunctionARN': function_version_arn,
                            'EventType': 'origin-request',
                            'IncludeBody': False
                        },
                    ]
                },
                'CachePolicyId': '658327ea-f89d-4fab-a63d-7e88639e58f6',
                'OriginRequestPolicyId': '88a5eaf4-2fd4-4709-b370-b4c650ea3fcf',
            },
            'Comment': 'Test Distribution',
            'PriceClass': 'PriceClass_All',
            'Enabled': True,
            'HttpVersion': 'http2and3',
        }
    )
    
    return create_distribution_response


if __name__ == "__main__":
    function_version_arn = deploy_function_version().get('FunctionArn')
    distribution = create_cloudfront_distribution(function_version_arn)
    
    print('successfully created CloudFront distribution {0}'.format(distribution.get('Distribution').get('Id')))