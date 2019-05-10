"""
This module implements a TD3 model.

TD3, or Twin Delayed Deep Deterministic Policy Gradient, uses actor-critic
method to optimize the policy and reward prediction. Notably, it uses the
minimum value of two critics instead of one to limit overestimation.
"""

import numpy as np
import tensorflow as tf

from garage.misc.overrides import overrides
from garage.tf.algos import DDPG


class TD3(DDPG):
    """
    Implementation of TD3.

    Based on https://arxiv.org/pdf/1802.09477.pdf.

    Example:
        $ python garage/examples/tf/td3_pendulum.py
    """

    def __init__(self,
                 env_spec,
                 policy,
                 qf,
                 qf2,
                 replay_buffer,
                 target_update_tau=0.01,
                 policy_lr=1e-4,
                 qf_lr=1e-3,
                 policy_weight_decay=0,
                 qf_weight_decay=0,
                 policy_optimizer=tf.train.AdamOptimizer,
                 qf_optimizer=tf.train.AdamOptimizer,
                 clip_pos_returns=False,
                 clip_return=np.inf,
                 discount=0.99,
                 max_action=None,
                 name=None,
                 n_epoch_cycles=20,
                 max_path_length=None,
                 n_train_steps=50,
                 buffer_batch_size=64,
                 min_buffer_size=1e4,
                 rollout_batch_size=1,
                 reward_scale=1.,
                 input_include_goal=False,
                 smooth_return=True,
                 exploration_strategy=None):
        """
        Construct class.

        Args:
            env_spec(garage.envs.EnvSpec): Environment.
            policy(garage.tf.policies.base.Policy): Policy.
            qf(garage.tf.q_functions.QFunction): Q-function.
            qf2(garage.tf.q_functions.QFunction): Q function to use
            target_update_tau(float): Interpolation parameter for doing the
                soft target update.
            policy_lr(float): Learning rate for training policy network.
            qf_lr(float): Learning rate for training q value network.
            policy_weight_decay(float): L2 weight decay factor for parameters
                of the policy network.
            qf_weight_decay(float): L2 weight decay factor for parameters
                of the q value network.
            policy_optimizer(tf.python.training.optimizer.Optimizer):
                Optimizer for training policy network.
            qf_optimizer(tf.python.training.optimizer.Optimizer):
                Optimizer for training q function network.
            clip_pos_returns(boolean): Whether or not clip positive returns.
            clip_return(float): Clip return to be in [-clip_return,
                clip_return].
            discount(float): Discount factor for the cumulative return.
            max_action(float): Maximum action magnitude.
            name(str): Name of the algorithm shown in computation graph.
            n_epoch_cycles(int): Number of batches of samples in each epoch.
            max_path_length(int): Maximum length of a path.
            n_train_steps(int): Number of optimizations in each epoch cycle.
            buffer_batch_size(int): Size of replay buffer.
            min_buffer_size(int):
                Number of samples in replay buffer before first optimization.
            rollout_batch_size(int):
            reward_scale(float): Scale to reward.
            input_include_goal(bool):
                True if the environment entails a goal in observation.
            smooth_return(bool):
                If True, do statistics on all samples collection.
                Otherwise do statistics on one batch.
            exploration_strategy(
                garage.np.exploration_strategies.ExplorationStrategy):
                Exploration strategy.
        """
        self.qf2 = qf2

        super(TD3, self).__init__(
            env_spec=env_spec,
            policy=policy,
            qf=qf,
            replay_buffer=replay_buffer,
            target_update_tau=target_update_tau,
            policy_lr=policy_lr,
            qf_lr=qf_lr,
            policy_weight_decay=policy_weight_decay,
            qf_weight_decay=qf_weight_decay,
            policy_optimizer=policy_optimizer,
            qf_optimizer=qf_optimizer,
            clip_pos_returns=clip_pos_returns,
            clip_return=clip_return,
            discount=discount,
            max_action=max_action,
            name=name,
            n_epoch_cycles=n_epoch_cycles,
            max_path_length=max_path_length,
            n_train_steps=n_train_steps,
            buffer_batch_size=buffer_batch_size,
            min_buffer_size=min_buffer_size,
            rollout_batch_size=rollout_batch_size,
            reward_scale=reward_scale,
            input_include_goal=input_include_goal,
            smooth_return=smooth_return,
            exploration_strategy=exploration_strategy)

    @overrides
    def init_opt(self):
        """Build the loss function and init the optimizer."""
        with tf.name_scope(self.name, 'TD3'):
            # Create target policy (actor) and qf (critic) networks
            self.target_policy_f_prob_online, _, _ = self.policy.build_net(
                trainable=False, name='target_policy')
            self.target_qf_f_prob_online, _, _, _ = self.qf.build_net(
                trainable=False, name='target_qf')
            self.target_qf2_f_prob_online, _, _, _ = self.qf2.build_net(
                trainable=False, name='target_qf2')

            # Set up target init and update functions
            with tf.name_scope('setup_target'):
                policy_init_ops, policy_update_ops = self.get_target_ops(
                    self.policy.get_global_vars(),
                    self.policy.get_global_vars('target_policy'), self.tau)
                qf_init_ops, qf_update_ops = self.get_target_ops(
                    self.qf.get_global_vars(),
                    self.qf.get_global_vars('target_qf'), self.tau)
                qf2_init_ops, qf2_update_ops = self.get_target_ops(
                    self.qf2.get_global_vars(),
                    self.qf2.get_global_vars('target_qf2'), self.tau)
                target_init_op = policy_init_ops + qf_init_ops
                target_update_op = policy_update_ops + qf_update_ops
                target_init_op2 = policy_init_ops + qf2_init_ops
                target_update_op2 = (policy_update_ops + qf2_update_ops)

            with tf.name_scope('inputs'):
                if self.input_include_goal:
                    obs_dim = self.env_spec.observation_space.\
                        flat_dim_with_keys(['observation', 'desired_goal'])
                else:
                    obs_dim = self.env_spec.observation_space.flat_dim
                y = tf.placeholder(tf.float32, shape=(None, 1), name='input_y')
                obs = tf.placeholder(
                    tf.float32,
                    shape=(None, obs_dim),
                    name='input_observation')
                actions = tf.placeholder(
                    tf.float32,
                    shape=(None, self.env_spec.action_space.flat_dim),
                    name='input_action')

            # Set up policy training function
            next_action = self.policy.get_action_sym(obs, name='policy_action')
            qval = self.qf.get_qval_sym(
                obs, next_action, name='policy_action_qval')
            q2val = self.qf2.get_qval_sym(
                obs, next_action, name='policy_action_q2val')
            next_qval = tf.minimum(qval, q2val)
            with tf.name_scope('action_loss'):
                action_loss = -tf.reduce_mean(next_qval)
                if self.policy_weight_decay > 0.:
                    policy_reg = self.policy_weight_decay * tf.add_n([
                        tf.nn.l2_loss(v)
                        for v in self.policy.get_regularizable_vars()
                    ])
                    action_loss += policy_reg

            with tf.name_scope('minimize_action_loss'):
                policy_train_op = self.policy_optimizer(
                    self.policy_lr, name='PolicyOptimizer').minimize(
                        action_loss, var_list=self.policy.get_trainable_vars())

            # Set up qf training function
            qval = self.qf.get_qval_sym(obs, actions, name='q_value')
            q2val = self.qf2.get_qval_sym(obs, actions, name='q2_value')
            with tf.name_scope('qval_loss'):
                qval_loss = (tf.reduce_mean(tf.squared_difference(y, qval)) +
                             tf.reduce_mean(tf.squared_difference(y, q2val)))
                if self.qf_weight_decay > 0.:
                    qf_reg = self.qf_weight_decay * tf.add_n([
                        tf.nn.l2_loss(v)
                        for v in self.qf.get_regularizable_vars()
                    ])
                    qval_loss += qf_reg

            with tf.name_scope('minimize_qf_loss'):
                qf_train_op = self.qf_optimizer(
                    self.qf_lr, name='QFunctionOptimizer').minimize(
                        qval_loss, var_list=self.qf.get_trainable_vars())
                qf2_train_op = self.qf_optimizer(
                    self.qf_lr, name='QFunctionOptimizer').minimize(
                        qval_loss, var_list=self.qf2.get_trainable_vars())

            self.f_train_policy = tf.get_default_session().make_callable(
                fetches=[policy_train_op, action_loss], feed_list=[obs])

            self.f_train_qf = tf.get_default_session().make_callable(
                fetches=[qf_train_op, qval_loss, qval],
                feed_list=[y, obs, actions])

            self.f_init_target = tf.get_default_session().make_callable(
                target_init_op)

            self.f_update_target = tf.get_default_session().make_callable(
                target_update_op)

            self.f_train_qf2 = tf.get_default_session().make_callable(
                fetches=[qf2_train_op, qval_loss, q2val],
                feed_list=[y, obs, actions])

            self.f_init_target2 = tf.get_default_session().make_callable(
                target_init_op2)

            self.f_update_target2 = tf.get_default_session().make_callable(
                target_update_op2)

    @overrides
    def optimize_policy(self, itr, samples_data):
        """
        Perform algorithm optimizing.

        Returns:
            action_loss: Loss of action predicted by the policy network.
            qval_loss: Loss of q value predicted by the q network.
            ys: y_s.
            qval: Q value predicted by the q network.

        """
        transitions = self.replay_buffer.sample(self.buffer_batch_size)
        observations = transitions['observation']
        rewards = transitions['reward']
        actions = transitions['action']
        next_observations = transitions['next_observation']
        terminals = transitions['terminal']

        rewards = rewards.reshape(-1, 1)
        terminals = terminals.reshape(-1, 1)

        if self.input_include_goal:
            goals = transitions['goal']
            next_inputs = np.concatenate((next_observations, goals), axis=-1)
            inputs = np.concatenate((observations, goals), axis=-1)
        else:
            next_inputs = next_observations
            inputs = observations

        target_actions = self.target_policy_f_prob_online(next_inputs)
        target_qvals = self.target_qf_f_prob_online(next_inputs,
                                                    target_actions)
        target_q2vals = self.target_qf2_f_prob_online(next_inputs,
                                                      target_actions)
        target_qvals = np.minimum(target_qvals, target_q2vals)
        clip_range = (-self.clip_return,
                      0. if self.clip_pos_returns else self.clip_return)
        ys = np.clip(
            rewards + (1.0 - terminals) * self.discount * target_qvals,
            clip_range[0], clip_range[1])

        _, qval_loss, qval = self.f_train_qf(ys, inputs, actions)
        _, q2val_loss, q2val = self.f_train_qf2(ys, inputs, actions)

        if np.equal(q2val_loss, np.minimum(qval_loss, q2val_loss)):
            qval = q2val
        qval_loss = np.minimum(qval_loss, q2val_loss)

        _, action_loss = self.f_train_policy(inputs)

        self.f_update_target()
        self.f_update_target2()

        return qval_loss, ys, qval, action_loss
