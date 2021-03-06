# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


import tensorflow.contrib.eager as tfe
tfe.enable_eager_execution()


import math
import os
import random
import sys
import time
import random
import json

import numpy as np
from six.moves import xrange
import tensorflow as tf
import os
import logging
import numpy as np
from six.moves import xrange  # pylint: disable=redefined-builtin
from tensorflow.python.framework import ops
from tensorflow.python.framework import dtypes
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import embedding_ops
from tensorflow.python.ops import rnn
from tensorflow.python.ops import rnn_cell
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops.math_ops import sigmoid
from tensorflow.python.ops.math_ops import tanh
from gensim.models.word2vec import Word2Vec
URL = {'model_url':'/home/peng.qiu/QueryRewrite/dataset/one_word_model_size150_window7_sg0_hs0_mincount100.bin',
'data_csv':'/home/peng.qiu/QueryRewrite/dataset/E_all_0to1250620.csv',
'X_all':'/home/peng.qiu/QueryRewrite/dataset/X_v5.npy',
'Y_all':'/home/peng.qiu/QueryRewrite/dataset/Y_v5.npy',
'code_url':'/home/peng.qiu/nlc-master/',
'common_word':'/home/peng.qiu/QueryRewrite/dataset/common_word.txt',
'test_data':'/home/peng.qiu/QueryRewrite/dataset/test_set_sighan.csv',
'test_X':'/home/peng.qiu/QueryRewrite/dataset/test_X.npy',
'test_Y':'/home/peng.qiu/QueryRewrite/dataset/test_Y.npy',
'lmfile':'/home/peng.qiu/QueryRewrite/model/news_lm.arpa',
}
# 'X_all':'/home/peng.qiu/QueryRewrite/dataset/X_v5.npy',
# 'Y_all':'/home/peng.qiu/QueryRewrite/dataset/Y_v5.npy',
para = {'learning_rate':0.003,
'learning_rate_decay_factor':0.95,
'max_gradient_norm':10.0,
'dropout':0.15,
'batch_size':512,
'epochs':20,
'size':150,
'num_layers':3,
'max_seq_len':32,
"optimizer":"adam",
"print_every":10,
"data_dir":"/tmp",
"train_dir":"/home/peng.qiu/nlc-master/train_dir",
"tokenizer":"CHAR",
'alpha':0.3,
'beam_size':5,
}
logging.basicConfig(level=logging.INFO)
config = tf.ConfigProto(allow_soft_placement = True)
config.gpu_options.allow_growth = True
os.environ["CUDA_VISIBLE_DEVICES"] = "7"

exec(open(URL['code_url']+'util.py','r').read())

size = 150
num_layers = 3
max_gradient_norm = 10.0
batch_size = 128
learning_rate = 0.003
learning_rate_decay_factor = 0.95
dropout = 0.15
optimizer = 'adam'

class GRUCellAttn(rnn_cell.GRUCell):
  def __init__(self, num_units, encoder_output, scope=None):
    self.hs = encoder_output
    with vs.variable_scope(scope or type(self).__name__):
      with vs.variable_scope("Attn1"):
        hs2d = tf.reshape(self.hs, [-1, num_units])
        # phi_hs2d = tanh(rnn_cell._linear(hs2d, num_units, True, 1.0))
        phi_hs2d = tanh(tf.contrib.layers.fully_connected(hs2d,num_units))
        self.phi_hs = tf.reshape(phi_hs2d, tf.shape(self.hs))
    super(GRUCellAttn, self).__init__(num_units)

  def __call__(self, inputs, state, scope=None):
    gru_out, gru_state = super(GRUCellAttn, self).__call__(inputs, state, scope)
    with vs.variable_scope(scope or type(self).__name__):
      with vs.variable_scope("Attn2"):
        # gamma_h = tanh(rnn_cell._linear(gru_out, self._num_units, True, 1.0))
        gamma_h = tanh(tf.contrib.layers.fully_connected(gru_out,self._num_units))
      weights = tf.reduce_sum(self.phi_hs * gamma_h, reduction_indices=2, keep_dims=True)
      weights = tf.exp(weights - tf.reduce_max(weights, reduction_indices=0, keep_dims=True))
      weights = weights / (1e-6 + tf.reduce_sum(weights, reduction_indices=0, keep_dims=True))
      context = tf.reduce_sum(self.hs * weights, reduction_indices=0)
      with vs.variable_scope("AttnConcat"):
        # out = tf.nn.relu(rnn_cell._linear([context, gru_out], self._num_units, True, 1.0))
        out = tf.nn.relu(tf.contrib.layers.fully_connected([context, gru_out], self._num_units))
      self.attn_map = tf.squeeze(tf.slice(weights, [0, 0, 0], [-1, -1, 1]))
      return (out[0], out[1])

class NLCModel(object):
  def __init__(self, size, num_layers, max_gradient_norm, batch_size, learning_rate,
               learning_rate_decay_factor, dropout, forward_only=False, optimizer="adam"):

    self.size = size
    # self.vocab_size = vocab_size
    self.batch_size = batch_size
    self.num_layers = num_layers
    self.keep_prob_config = 1.0 - dropout
    self.learning_rate = tfe.Variable(float(learning_rate), trainable=False)
    self.learning_rate_decay_op = self.learning_rate.assign(self.learning_rate * learning_rate_decay_factor)
    self.global_step = tfe.Variable(0, trainable=False)

    self.keep_prob = 1
    self.source_tokens = source_tokens
    self.target_tokens = target_tokens
    self.source_mask = source_mask
    self.target_mask = target_mask
    self.beam_size = 8
    self.target_length = tf.reduce_sum(self.target_mask, reduction_indices=0)

    self.embedding,self.vocab_to_int,self.int_to_vocab,self.vocab_size,self.embed_size = self.get_embeddings()
    self.decoder_state_input, self.decoder_state_output = [], []
    for i in xrange(self.num_layers):
      # self.decoder_state_input.append(tf.get_variable())
      default_value = tf.get_variable('default_value',shape=[self.batch_size, self.size],dtype=tf.float32,trainable=True)
      self.decoder_state_input.append(default_value)
    # self.set_default_decoder_state_input(self.batch_size)

    with tf.variable_scope("NLC", initializer=tf.uniform_unit_scaling_initializer(1.0)):
      # self.embedding,_,_,_,_ = self.get_embeddings()
      self.setup_embeddings()
      self.setup_encoder()
      self.setup_decoder()
      self.setup_loss()

  def setup_encoder(self):
    self.encoder_cell = rnn_cell.GRUCell(self.size)
    with vs.variable_scope("PryamidEncoder"):
      inp = self.encoder_inputs
      mask = self.source_mask
      out = None
      for i in xrange(self.num_layers):
        with vs.variable_scope("EncoderCell%d" % i) as scope:
          srclen = tf.reduce_sum(mask, reduction_indices=0)
          out, output_state = self.bidirectional_rnn(self.encoder_cell, inp, srclen, scope=scope)
          dropin, mask = self.downscale(out, mask)
          inp = self.dropout(dropin)
      self.encoder_output = out

  def setup_decoder(self):
    self.decoder_state_output =[]
    if self.num_layers > 1:
      self.decoder_cell = rnn_cell.GRUCell(self.size)
      # self.decoder_cell = tf.contrib.rnn.GRUCell(self.size,kernel_initializer = tf.contrib.layers.xavier_initializer(dtype=tf.float32))
    self.attn_cell = GRUCellAttn(self.size, self.encoder_output, scope="DecoderAttnCell")

    with vs.variable_scope("Decoder"):
      inp = self.decoder_inputs
      for i in xrange(self.num_layers - 1):
        with vs.variable_scope("DecoderCell%d" % i) as scope:
          out, state_output = rnn.dynamic_rnn(self.decoder_cell, inp, time_major=True,
                                              dtype=dtypes.float32, sequence_length=self.target_length,
                                              scope=scope)
          inp = self.dropout(out)
          self.decoder_state_output.append(state_output)

      with vs.variable_scope("DecoderAttnCell") as scope:
        out, state_output = rnn.dynamic_rnn(self.attn_cell, inp, time_major=True,
                                            dtype=dtypes.float32, sequence_length=self.target_length,
                                            scope=scope)
        self.decoder_output = self.dropout(out)
        self.decoder_state_output.append(state_output)

  def get_embeddings(self):
    #special token setting
    model = Word2Vec.load(URL['model_url'])  
    common_words = open(URL['common_word'],'r').readlines()
    special_words = ['<PAD>', '<UNK>', '<GO>',  '<EOS>']
    special_words_int = [0,1,2,3]

    vocab_to_int = {}
    for i in range(len(special_words)):
      vocab_to_int[special_words[i]] = special_words_int[i]

    id = 4
    for word in common_words:
      word = word.split()[0]
      vocab_to_int[word] = id
      id+=1
    weights = model.wv.syn0  

    int_to_vocab = {}
    for key,value in vocab_to_int.items():
      int_to_vocab[value] = key

    #size
    vocab_size = len(vocab_to_int)
    embed_size = len(weights[0])

    #set embedding 
    Embeddings = np.zeros((vocab_size, embed_size))
    for k, v in vocab_to_int.items():
      if v not in special_words_int:
        Embeddings[v] = model[k]
    for i in special_words_int:
      # np.random.seed(-i)
      Embeddings[i] = np.random.random([embed_size,])
    Embeddings = Embeddings.astype('float32')
    return Embeddings,vocab_to_int,int_to_vocab,vocab_size,embed_size

  def setup_embeddings(self):
    with vs.variable_scope("embeddings"):
      self.encoder_inputs = embedding_ops.embedding_lookup(self.embedding, self.source_tokens)
      self.decoder_inputs = embedding_ops.embedding_lookup(self.embedding, self.target_tokens)
  def bidirectional_rnn(self, cell, inputs, lengths, scope=None):
    name = scope.name or "BiRNN"
    # Forward direction
    with vs.variable_scope(name + "_FW") as fw_scope:
      output_fw, output_state_fw = rnn.dynamic_rnn(cell, inputs, time_major=True, dtype=dtypes.float32,
                                                   sequence_length=lengths, scope=fw_scope)
    # Backward direction
    inputs_bw = tf.reverse_sequence(inputs, tf.to_int64(lengths), seq_dim=0, batch_dim=1)
    with vs.variable_scope(name + "_BW") as bw_scope:
      output_bw, output_state_bw = rnn.dynamic_rnn(cell, inputs_bw, time_major=True, dtype=dtypes.float32,
                                                   sequence_length=lengths, scope=bw_scope)

    output_bw = tf.reverse_sequence(output_bw, tf.to_int64(lengths), seq_dim=0, batch_dim=1)

    outputs = output_fw + output_bw
    output_state = output_state_fw + output_state_bw

    return (outputs, output_state)

  def downscale(self, inp, mask):
    # return inp, mask

    with vs.variable_scope("Downscale"):
      inshape = tf.shape(inp)
      T, batch_size, dim = inshape[0], inshape[1], inshape[2]
      inp2d = tf.reshape(tf.transpose(inp, perm=[1, 0, 2]), [-1, 2 * self.size])
      # out2d = rnn_cell._linear(inp2d, self.size, True, 1.0)
      out2d = tf.contrib.layers.fully_connected(inp2d,self.size)
      out3d = tf.reshape(out2d, tf.stack((batch_size, tf.to_int32(T/2), dim)))
      out3d = tf.transpose(out3d, perm=[1, 0, 2])
      out3d.set_shape([None, None, self.size])
      out = tanh(out3d)

      mask = tf.transpose(mask)
      mask = tf.reshape(mask, [-1, 2])
      mask = tf.cast(mask, tf.bool)
      mask = tf.reduce_any(mask, reduction_indices=1)
      # logical or 
      mask = tf.to_int32(mask)
      mask = tf.reshape(mask, tf.stack([batch_size, -1]))
      mask = tf.transpose(mask)
    return out, mask

  def dropout(self, inp):
    return tf.nn.dropout(inp, self.keep_prob)

  # def set_default_decoder_state_input(self, input_feed, batch_size):
  #   default_value = np.zeros([batch_size, self.size])
  #   for i in xrange(self.num_layers):
  #     input_feed[self.decoder_state_input[i]] = default_value

  def set_default_decoder_state_input(self, batch_size):
    default_value = np.zeros([batch_size, self.size])
    self.decoder_state_input = [] 
    for i in xrange(self.num_layers):
      self.decoder_state_input.append(default_value)
    self.decoder_state_input = np.array(self.decoder_state_input)


  def decoder_graph(self, decoder_inputs, decoder_state_input):
    decoder_output, decoder_state_output = None, []
    inp = decoder_inputs
    # self.attn_cell_2 = 
    with vs.variable_scope("Decoder", reuse=True):
      for i in xrange(self.num_layers - 1):
        with vs.variable_scope("DecoderCell%d" % i) as scope:
          inp, state_output = self.decoder_cell(inp, decoder_state_input[i])
          decoder_state_output.append(state_output)

      with vs.variable_scope("DecoderAttnCell") as scope:
        decoder_output, state_output = self.attn_cell(inp, decoder_state_input[i+1])
        decoder_state_output.append(state_output)

    return decoder_output, decoder_state_output

  def setup_loss(self):
    with vs.variable_scope("Logistic"):
      doshape = tf.shape(self.decoder_output)
      T, batch_size = doshape[0], doshape[1]
      do2d = tf.reshape(self.decoder_output, [-1, self.size])
      self.do2d = do2d
      # logits2d = rnn_cell._linear(do2d, self.vocab_size, True, 1.0)
      logits2d = tf.contrib.layers.fully_connected(do2d,self.vocab_size,weights_initializer=tf.contrib.layers.xavier_initializer(dtype=tf.float32))
      # reuse?
      outputs2d = tf.nn.log_softmax(logits2d)
      self.outputs = tf.reshape(outputs2d, tf.stack([T, batch_size, self.vocab_size]))

      targets_no_GO = tf.slice(self.target_tokens, [1, 0], [-1, -1])
      masks_no_GO = tf.slice(self.target_mask, [1, 0], [-1, -1])
      # easier to pad target/mask than to split decoder input since tensorflow does not support negative indexing
      labels1d = tf.reshape(tf.pad(targets_no_GO, [[0, 1], [0, 0]]), [-1])
      mask1d = tf.reshape(tf.pad(masks_no_GO, [[0, 1], [0, 0]]), [-1])
      losses1d = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=logits2d, labels=labels1d) * tf.to_float(mask1d)
      losses2d = tf.reshape(losses1d, tf.stack([T, batch_size]))
      self.losses = tf.reduce_sum(losses2d) / tf.to_float(batch_size)

  def setup_beam(self):
    time_0 = tf.constant(0)
    beam_seqs_0 = tf.constant([[SOS_ID]])
    beam_probs_0 = tf.constant([0.])

    cand_seqs_0 = tf.constant([[EOS_ID]])
    cand_probs_0 = tf.constant([-3e38])

    state_0 = tf.zeros([1, self.size])
    states_0 = [state_0] * self.num_layers

    time = tf.constant(0)
    beam_seqs = tf.constant([[SOS_ID]])
    beam_probs = tf.constant([0.])

    cand_seqs = tf.constant([[EOS_ID]])
    cand_probs = tf.constant([-3e38])

    state = tf.zeros([1, self.size])
    states = [state] * self.num_layers

  def train(self, source_tokens, source_mask, target_tokens, target_mask,num_gpu=None):
    if num_gpu ==None:
      input_feed = {}
      input_feed[self.source_tokens] = source_tokens
      # self.source_tokens here must be a tensor 
      input_feed[self.target_tokens] = target_tokens
      input_feed[self.source_mask] = source_mask
      input_feed[self.target_mask] = target_mask
      input_feed[self.keep_prob] = self.keep_prob_config
      self.input_feed = input_feed




    def beam_cond(time, beam_probs, beam_seqs, cand_probs, cand_seqs, *states):
      return tf.reduce_max(beam_probs) >= tf.reduce_min(cand_probs)

    def beam_step(time, beam_probs, beam_seqs, cand_probs, cand_seqs, *states):
      batch_size = tf.shape(beam_probs)[0]
      inputs = tf.reshape(tf.slice(beam_seqs, [0, time], [batch_size, 1]), [batch_size])
      decoder_input = embedding_ops.embedding_lookup(self.embedding, inputs)
      decoder_output, state_output = self.decoder_graph(decoder_input, states)

      with vs.variable_scope("Logistic", reuse=True):
        do2d = tf.reshape(decoder_output, [-1, self.size])
        # logits2d = rnn_cell._linear(do2d, self.vocab_size, True, 1.0)
        logits2d = tf.contrib.layers.fully_connected(do2d,self.vocab_size)

        logprobs2d = tf.nn.log_softmax(logits2d)

      total_probs = logprobs2d + tf.reshape(beam_probs, [-1, 1])
      total_probs_noEOS = tf.concat(1, [tf.slice(total_probs, [0, 0], [batch_size, EOS_ID]),
                                        tf.tile([[-3e38]], [batch_size, 1]),
                                        tf.slice(total_probs, [0, EOS_ID + 1],
                                                 [batch_size, self.vocab_size - EOS_ID - 1])])

      flat_total_probs = tf.reshape(total_probs_noEOS, [-1])
      beam_k = tf.minimum(tf.size(flat_total_probs), self.beam_size)
      next_beam_probs, top_indices = tf.nn.top_k(flat_total_probs, k=beam_k)

      next_bases = tf.floordiv(top_indices, self.vocab_size)
      next_mods = tf.mod(top_indices, self.vocab_size)

      next_states = [tf.gather(state, next_bases) for state in state_output]
      next_beam_seqs = tf.concat(1, [tf.gather(beam_seqs, next_bases),
                                     tf.reshape(next_mods, [-1, 1])])

      cand_seqs_pad = tf.pad(cand_seqs, [[0, 0], [0, 1]])
      beam_seqs_EOS = tf.pad(beam_seqs, [[0, 0], [0, 1]])
      new_cand_seqs = tf.concat(0, [cand_seqs_pad, beam_seqs_EOS])
      EOS_probs = tf.slice(total_probs, [0, EOS_ID], [batch_size, 1])
      new_cand_probs = tf.concat(0, [cand_probs, tf.reshape(EOS_probs, [-1])])

      cand_k = tf.minimum(tf.size(new_cand_probs), self.beam_size)
      next_cand_probs, next_cand_indices = tf.nn.top_k(new_cand_probs, k=cand_k)
      next_cand_seqs = tf.gather(new_cand_seqs, next_cand_indices)

      return [time + 1, next_beam_probs, next_beam_seqs, next_cand_probs, next_cand_seqs] + next_states

    var_shape = []
    var_shape.append((time_0, time_0.get_shape()))
    var_shape.append((beam_probs_0, tf.TensorShape([None,])))
    var_shape.append((beam_seqs_0, tf.TensorShape([None, None])))
    var_shape.append((cand_probs_0, tf.TensorShape([None,])))
    var_shape.append((cand_seqs_0, tf.TensorShape([None, None])))
    var_shape.extend([(state_0, tf.TensorShape([None, self.size])) for state_0 in states_0])
    loop_vars, loop_var_shapes = zip(* var_shape)
    ret_vars = tf.while_loop(cond=beam_cond, body=beam_step, loop_vars=loop_vars, shape_invariants=loop_var_shapes, back_prop=False)
#    time, beam_probs, beam_seqs, cand_probs, cand_seqs, _ = ret_vars
    cand_seqs = ret_vars[4]
    cand_probs = ret_vars[3]
    self.beam_output = cand_seqs
    self.beam_scores = cand_probs
x_train, y_train, x_dev, y_dev = prepare_nlc_data()
X = x_train
Y = y_train
i=0
x_tokens = X[i*batch_size:(i+1)*batch_size]
y_tokens = Y[i*batch_size:(i+1)*batch_size]
y_tokens = add_sos_eos(y_tokens)
x_padded, y_padded = padded(x_tokens, num_layers), padded(y_tokens, 1)

source_tokens = np.array(x_padded).T
source_mask = (source_tokens != PAD_ID).astype(np.int32)
target_tokens = np.array(y_padded).T
target_mask = (target_tokens != PAD_ID).astype(np.int32)

model = NLCModel(
      size, num_layers, max_gradient_norm, batch_size,
      learning_rate, learning_rate_decay_factor, dropout,
      forward_only=False, optimizer=optimizer)
self = model



model = NLCModel(
  para["size"], para["num_layers"], para["max_gradient_norm"], para["batch_size"],
  para["learning_rate"], para["learning_rate_decay_factor"], para["dropout"],
  forward_only=False, optimizer=para["optimizer"])

output_labels = tf.argmax(self.outputs,axis=2)
