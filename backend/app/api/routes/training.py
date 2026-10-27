from fastapi import APIRouter, HTTPException
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from app.services import managed_training

router=APIRouter(prefix='/training',tags=['managed training'])


class StartInput(BaseModel):
    confirm_billable_training: bool


class PredictInput(BaseModel):
    horizon_days: int=Field(default=90,ge=1,le=365)


def invoke(function,*args):
    try:return function(*args)
    except ClientError as error:
        code=error.response['Error']['Code']
        raise HTTPException(503,f'AWS training request failed ({code}). The existing website and saved forecasts remain available.') from error
    except (ValueError,KeyError) as error:raise HTTPException(422,str(error)) from error


@router.get('/status')
def status():return invoke(managed_training.training_status)


@router.post('/jobs',status_code=202)
def start(payload:StartInput):return invoke(managed_training.start_training,payload.confirm_billable_training)


@router.get('/jobs/{name}')
def job(name:str):return invoke(managed_training.job_status,name)


@router.post('/jobs/{name}/activate')
def activate(name:str):return invoke(managed_training.activate,name)


@router.post('/predict',status_code=201)
def predict(payload:PredictInput):return invoke(managed_training.predict_active,payload.horizon_days)
