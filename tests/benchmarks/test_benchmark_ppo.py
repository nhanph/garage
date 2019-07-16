'''
This script creates a regression test over garage-PPO and baselines-PPO.

Unlike garage, baselines doesn't set max_path_length. It keeps steps the action
until it's done. So we introduced tests.wrappers.AutoStopEnv wrapper to set
done=True when it reaches max_path_length.
'''
import datetime
import multiprocessing
import os.path as osp
import random

from baselines import bench
from baselines import logger as baselines_logger
from baselines.bench import benchmarks
from baselines.common import set_global_seeds
from baselines.common.vec_env.dummy_vec_env import DummyVecEnv
from baselines.common.vec_env.vec_normalize import VecNormalize
from baselines.logger import configure
from baselines.ppo2 import ppo2
from baselines.ppo2.policies import MlpPolicy
import dowel
from dowel import logger as dowel_logger
import gym
import pytest
import tensorflow as tf

from garage.envs import normalize
from garage.experiment import deterministic
from garage.experiment import LocalRunner
from garage.tf.algos import PPO
from garage.tf.baselines import GaussianMLPBaseline
from garage.tf.envs import TfEnv
from garage.tf.optimizers import FirstOrderOptimizer
from garage.tf.policies import GaussianMLPPolicy
from garage.tf.policies import GaussianMLPPolicyWithModel
import tests.helpers as Rh

var1 = []
var2 = []


class TestBenchmarkPPO:
    '''Compare benchmarks between garage and baselines.'''

    @pytest.mark.huge
    def test_benchmark_ppo(self):
        '''
        Compare benchmarks between garage and baselines.

        :return:
        '''
        mujoco1m = benchmarks.get_benchmark('Mujoco1M')
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')
        benchmark_dir = './data/local/benchmarks/ppo/%s/' % timestamp
        result_json = {}
        for task in mujoco1m['tasks']:
            env_id = task['env_id']
            if 'HalfCheetah' not in env_id:
                continue
            env = gym.make(env_id)
            # baseline_env = AutoStopEnv(env_name=env_id, max_path_length=100)

            seeds = random.sample(range(100), task['trials'])

            task_dir = osp.join(benchmark_dir, env_id)
            plt_file = osp.join(benchmark_dir,
                                '{}_benchmark.png'.format(env_id))
            garage_models_csvs = []
            garage_csvs = []

            for trial in range(task['trials']):
                seed = 88

                trial_dir = task_dir + '/trial_%d_seed_%d' % (trial + 1, seed)
                garage_dir = trial_dir + '/garage'
                garage_models_dir = trial_dir + '/garage_models'

                with tf.Graph().as_default():
                    # Run baselines algorithms
                    # baseline_env.reset()
                    # baselines_csv = run_baselines(baseline_env, seed,
                    #                               baselines_dir)

                    # Run garage algorithms
                    env.reset()
                    env.seed(1)
                    x = env.reset()
                    print(x)
                    garage_csv = run_garage(env, seed, garage_dir)
                    env.reset()
                    env.seed(1)
                    x = env.reset()
                    print(x)
                    garage_models_csv = run_garage_with_models(
                        env, seed, garage_models_dir)
                garage_csvs.append(garage_csv)
                garage_models_csvs.append(garage_models_csv)

            env.close()

            Rh.plot(
                b_csvs=garage_models_csvs,
                g_csvs=garage_csvs,
                g_x='Iteration',
                g_y='AverageReturn',
                b_x='Iteration',
                b_y='AverageReturn',
                trials=task['trials'],
                seeds=seeds,
                plt_file=plt_file,
                env_id=env_id,
                x_label='Iteration',
                y_label='AverageReturn')

            result_json[env_id] = Rh.create_json(
                b_csvs=garage_models_csvs,
                g_csvs=garage_csvs,
                seeds=seeds,
                trails=task['trials'],
                g_x='Iteration',
                g_y='AverageReturn',
                b_x='Iteration',
                b_y='AverageReturn',
                factor_g=2048,
                factor_b=2048)

        Rh.write_file(result_json, 'PPO')


def run_garage(env, seed, log_dir):
    '''
    Create garage model and training.

    Replace the ppo with the algorithm you want to run.

    :param env: Environment of the task.
    :param seed: Random seed for the trial.
    :param log_dir: Log dir path.
    :return:
    '''
    global var1
    var1 = []
    deterministic.set_seed(seed)
    config = tf.ConfigProto(
        allow_soft_placement=True,
        intra_op_parallelism_threads=12,
        inter_op_parallelism_threads=12)
    sess = tf.Session(config=config)
    with LocalRunner(sess=sess, max_cpus=12) as runner:
        env = TfEnv(normalize(env))

        policy = GaussianMLPPolicy(
            env_spec=env.spec,
            hidden_sizes=(32, 32),
            hidden_nonlinearity=tf.nn.tanh,
            output_nonlinearity=None,
        )

        baseline = GaussianMLPBaseline(
            env_spec=env.spec,
            regressor_args=dict(
                hidden_sizes=(64, 64),
                use_trust_region=False,
                optimizer=FirstOrderOptimizer,
                optimizer_args=dict(
                    batch_size=32,
                    max_epochs=10,
                    tf_optimizer_args=dict(learning_rate=1e-3),
                ),
            ),
        )

        algo = PPO(
            env_spec=env.spec,
            policy=policy,
            baseline=baseline,
            max_path_length=100,
            discount=0.99,
            gae_lambda=0.95,
            lr_clip_range=0.2,
            policy_ent_coeff=0.0,
            optimizer_args=dict(
                batch_size=32,
                max_epochs=10,
                tf_optimizer_args=dict(learning_rate=1e-3),
            ),
        )

        # Set up logger since we are not using run_experiment
        tabular_log_file = osp.join(log_dir, 'progress.csv')
        dowel_logger.add_output(dowel.StdOutput())
        dowel_logger.add_output(dowel.CsvOutput(tabular_log_file))
        dowel_logger.add_output(dowel.TensorBoardOutput(log_dir))

        runner.setup(algo, env, sampler_args=dict(n_envs=12))
        r1 = policy.get_params()
        # Save weights
        var1.extend([i.eval() for i in r1])
        runner.train(n_epochs=3, batch_size=2048)
        dowel_logger.remove_all()

        return tabular_log_file


def run_garage_with_models(env, seed, log_dir):
    '''
    Create garage model and training.

    Replace the ppo with the algorithm you want to run.

    :param env: Environment of the task.
    :param seed: Random seed for the trial.
    :param log_dir: Log dir path.
    :return:
    '''
    global var2
    deterministic.set_seed(seed)
    config = tf.ConfigProto(
        allow_soft_placement=True,
        intra_op_parallelism_threads=12,
        inter_op_parallelism_threads=12)
    sess = tf.Session(config=config)
    with LocalRunner(sess=sess, max_cpus=12) as runner:
        env = TfEnv(normalize(env))

        policy = GaussianMLPPolicyWithModel(
            env_spec=env.spec,
            name='GaussianMLPPolicyBenchmark',
            hidden_sizes=(32, 32),
            hidden_nonlinearity=tf.nn.tanh,
            output_nonlinearity=None,
        )

        baseline = GaussianMLPBaseline(
            env_spec=env.spec,
            regressor_args=dict(
                hidden_sizes=(64, 64),
                use_trust_region=False,
                optimizer=FirstOrderOptimizer,
                optimizer_args=dict(
                    batch_size=32,
                    max_epochs=10,
                    tf_optimizer_args=dict(learning_rate=1e-3),
                ),
            ),
            name='GaussianMLPBaselineBenchmark')

        algo = PPO(
            env_spec=env.spec,
            policy=policy,
            baseline=baseline,
            max_path_length=100,
            discount=0.99,
            gae_lambda=0.95,
            lr_clip_range=0.2,
            policy_ent_coeff=0.0,
            optimizer_args=dict(
                batch_size=32,
                max_epochs=10,
                tf_optimizer_args=dict(learning_rate=1e-3),
            ),
            name='PPOBenchmark')

        # Set up logger since we are not using run_experiment
        tabular_log_file = osp.join(log_dir, 'progress.csv')
        dowel_logger.add_output(dowel.StdOutput())
        dowel_logger.add_output(dowel.CsvOutput(tabular_log_file))
        dowel_logger.add_output(dowel.TensorBoardOutput(log_dir))

        runner.setup(algo, env, sampler_args=dict(n_envs=12))
        r2 = policy.get_trainable_vars()
        # Load weights that was saved from GaussianMLPPolicy
        for i in range(len(r2)):
            r2[i].load(var1[i])
        runner.train(n_epochs=3, batch_size=2048)
        var2.append([i.eval() for i in r2])
        dowel_logger.remove_all()

        return tabular_log_file


def run_baselines(env, seed, log_dir):
    '''
    Create baselines model and training.

    Replace the ppo and its training with the algorithm you want to run.

    :param env: Environment of the task.
    :param seed: Random seed for the trial.
    :param log_dir: Log dir path.
    :return
    '''
    ncpu = max(multiprocessing.cpu_count() // 2, 1)
    config = tf.ConfigProto(
        allow_soft_placement=True,
        intra_op_parallelism_threads=ncpu,
        inter_op_parallelism_threads=ncpu)
    tf.Session(config=config).__enter__()

    # Set up logger for baselines
    configure(dir=log_dir, format_strs=['stdout', 'log', 'csv', 'tensorboard'])
    baselines_logger.info('rank {}: seed={}, logdir={}'.format(
        0, seed, baselines_logger.get_dir()))

    def make_env():
        monitor = bench.Monitor(
            env, baselines_logger.get_dir(), allow_early_resets=True)
        return monitor

    env = DummyVecEnv([make_env])
    env = VecNormalize(env)

    set_global_seeds(seed)
    policy = MlpPolicy
    ppo2.learn(
        policy=policy,
        env=env,
        nsteps=2048,
        nminibatches=32,
        lam=0.95,
        gamma=0.99,
        noptepochs=10,
        log_interval=1,
        ent_coef=0.0,
        lr=1e-3,
        vf_coef=0.5,
        max_grad_norm=None,
        cliprange=0.2,
        total_timesteps=int(1e6))

    return osp.join(log_dir, 'progress.csv')
