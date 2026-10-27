"""Small, serializable demand models with chronological validation and test splits."""
import math
from datetime import timedelta


def _features(history, day, origin):
    return [1.0, (day-origin).days/365.0,
            math.sin(2*math.pi*day.weekday()/7), math.cos(2*math.pi*day.weekday()/7),
            history[-1], history[-7], sum(history[-7:])/7]


def _solve(matrix, target):
    a = [list(row)+[value] for row, value in zip(matrix, target)]
    for col in range(len(a)):
        pivot = max(range(col, len(a)), key=lambda row: abs(a[row][col]))
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        if abs(scale) < 1e-12:
            raise ValueError('Singular demand model')
        a[col] = [v/scale for v in a[col]]
        for row in range(len(a)):
            if row != col:
                factor = a[row][col]
                a[row] = [v-factor*w for v, w in zip(a[row], a[col])]
    return [row[-1] for row in a]


def fit(series, algorithm):
    values = [float(q) for _, q in series]
    model = {'algorithm': algorithm, 'origin': series[0][0].isoformat(),
             'last_date': series[-1][0].isoformat(), 'history': values[-56:]}
    if algorithm == 'weekday_mean':
        groups = {day: [] for day in range(7)}
        for day, quantity in series[-56:]:
            groups[day.weekday()].append(float(quantity))
        model['weekday_means'] = {str(day): sum(q)/len(q) if q else 0.0 for day,q in groups.items()}
    if algorithm.startswith('seasonal_naive_'):
        model['period'] = int(algorithm.rsplit('_',1)[1])
        if len(values) < model['period']:
            raise ValueError('Not enough history for the seasonal period')
    if algorithm == 'ridge_lag_calendar':
        x = [_features(values[:i], series[i][0], series[0][0]) for i in range(7, len(values))]
        y = values[7:]
        matrix = [[sum(row[i]*row[j] for row in x)+(1.0 if i == j and i else 0.0)
                   for j in range(7)] for i in range(7)]
        model['coefficients'] = _solve(matrix, [sum(row[i]*v for row, v in zip(x,y)) for i in range(7)])
    return model


def predict(model, horizon):
    from datetime import date
    values = list(model['history'])
    origin = date.fromisoformat(model['origin'])
    last = date.fromisoformat(model['last_date'])
    result = []
    for offset in range(1, horizon+1):
        day = last+timedelta(days=offset)
        if model['algorithm'] == 'ridge_lag_calendar':
            value = sum(a*b for a,b in zip(model['coefficients'], _features(values,day,origin)))
        elif model['algorithm'] == 'weekday_mean':
            value = model['weekday_means'][str(day.weekday())]
        elif model['algorithm'].startswith('seasonal_naive_'):
            value = values[-model['period']]
        elif model['algorithm'] == 'weekly_seasonal_naive':
            value = values[-7]
        elif model['algorithm'] == 'linear_trend_regression':
            recent = model['history'][-7:]
            n = len(recent); mx = (n-1)/2; my = sum(recent)/n
            denom = sum((i-mx)**2 for i in range(n))
            slope = sum((i-mx)*(q-my) for i,q in enumerate(recent))/denom if denom else 0
            value = my+slope*(n-1+offset-mx)
        else:
            value = sum(model['history'][-7:])/len(model['history'][-7:])
        if not math.isfinite(value):
            raise ValueError('Non-finite demand prediction')
        value = max(0.0,value)
        values.append(value); result.append(round(value,4))
    return result


def metrics(actuals, predictions):
    errors = [abs(a-p) for a,p in zip(actuals,predictions)]
    total = sum(actuals)
    return {'mae': round(sum(errors)/len(errors),4),
            'rmse': round(math.sqrt(sum(e*e for e in errors)/len(errors)),4),
            'wape': round(sum(errors)/total,4) if total else None,
            'holdout_observations':len(errors)}


def train(series, horizon=7):
    """Select on validation only; report accuracy on a separate final test period."""
    if len(series)<4:
        raise ValueError('At least four complete daily observations are required')
    if horizon < 1 or horizon > 365:
        raise ValueError('Forecast horizon must be between 1 and 365 days')
    if any(not math.isfinite(float(q)) or q < 0 for _,q in series):
        raise ValueError('Sales quantities must be finite and nonnegative')
    if any((series[i][0]-series[i-1][0]).days != 1 for i in range(1,len(series))):
        raise ValueError('Sales dates must be consecutive and unique')
    test_size = max(1,min(14,len(series)//5))
    development, test = series[:-test_size], series[-test_size:]
    scores = {}
    algorithm = 'recent_mean'
    if len(development)>=28:
        validation_size = min(14,len(development)//4)
        training, validation = development[:-validation_size], development[-validation_size:]
        # Fixed retail candidates; never choose periods or tune parameters using test targets.
        # Weekday averaging reduces the variance of sparse weekly invoice quantities.
        candidates = ['recent_mean','linear_trend_regression','weekday_mean','weekly_seasonal_naive']
        for period in [14,21,28]:
            if len(training) >= 2*period:
                candidates.append(f'seasonal_naive_{period}')
        if len(training)>=28:
            candidates.append('ridge_lag_calendar')
        for candidate in candidates:
            scores[candidate] = metrics([q for _,q in validation],predict(fit(training,candidate),len(validation)))['mae']
        algorithm = min(scores,key=scores.get)
    test_predictions = predict(fit(development,algorithm),test_size)
    evaluation = metrics([q for _,q in test],test_predictions)
    evaluation.update({'evaluation':'chronological_validation_then_untouched_test',
                       'evaluation_model_name':algorithm,'validation_mae':scores,
                       'test_start':test[0][0].isoformat(),'test_end':test[-1][0].isoformat(),
                       'limited_history':len(series)<56})
    model = fit(series,algorithm)
    return {'model':model,'metrics':evaluation,'predictions':predict(model,horizon),
            'test_predictions':test_predictions,'test_actuals':[q for _,q in test]}
