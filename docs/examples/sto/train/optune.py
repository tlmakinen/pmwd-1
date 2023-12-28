"""Hyperparameter optimization using optuna."""
import optuna
from optuna.samplers import TPESampler
import optax
import numpy as np
import joblib

from run_train import prep_train, run_train, slurm_job_id, procid
from pmwd.sto.so import soft_len
from pmwd.sto.mlp import init_mlp_params


def objective(trial, sobol_ids, gsdata, snap_ids):
    n_epochs = 100
    shuffle_epoch = True
    so_type = 'NN'
    n_input = [soft_len(k_fac=3), soft_len()]

    # hypars to search
    opt_name = trial.suggest_categorical('optimizer', ['Adam', 'AdamW', 'SGD'])
    learning_rate = trial.suggest_float('learning_rate', 1e-6, 1e-3, log=True)
    if opt_name == 'Adam':
        b1 = trial.suggest_float('b1', 0.5, 0.9)
        b2 = trial.suggest_float('b2', 0.5, 0.999)
        optimizer = optax.adam(learning_rate, b1=b1, b2=b2)
    if opt_name == 'AdamW':
        b1 = trial.suggest_float('b1', 0.5, 0.9)
        weight_decay = trial.suggest_float('weight_decay', 1e-5, 1e-3, log=True)
        optimizer = optax.adamw(learning_rate, b1=b1, weight_decay=weight_decay)
    if opt_name == 'SGD':
        optimizer = optax.sgd(learning_rate)
    n_layers = trial.suggest_int('n_layers', 2, 8)

    so_nodes = [[n] * n_layers + [1] for n in n_input]
    so_params = init_mlp_params(n_input, so_nodes, scheme='last_ws')

    # train for a fixed number of epochs and get the lowest loss as the obj
    losses = run_train(n_epochs, sobol_ids, gsdata, snap_ids, shuffle_epoch,
                       learning_rate, optimizer, so_type, so_nodes, so_params,
                       ret=True, log_id=trial.number, verbose=False)
    losses = np.array(losses)

    return losses.min()


def optune(n_trials):
    sampler = TPESampler(seed=0)  # to make the pars suggested by Optuna the same for all procs
    if procid == 0:
        storage_name = f'sqlite:///study/{slurm_job_id}.db'
    else:
        storage_name = None
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(sampler=sampler, study_name=f'{slurm_job_id}',
                                storage=storage_name)

    sobol_ids_global = np.arange(0, 16)
    snap_ids = np.arange(0, 121, 1)
    sobol_ids, gsdata = prep_train(sobol_ids_global, snap_ids)
    study.optimize(lambda trial: objective(trial, sobol_ids, gsdata, snap_ids),
                   n_trials=n_trials)

    return study


if __name__ == "__main__":
    study = optune(20)
    if procid == 0:
        joblib.dump(study, f'study/{slurm_job_id}.pkl')
