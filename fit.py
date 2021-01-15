import sys, time
import numpy as np
import tensorflow as tf
from . import metrics


#------------------------------------------------------------------------------------------
# Custom fits
#------------------------------------------------------------------------------------------

def fit_lr_decay(model, x_train, y_train, validation_data, metrics=['loss', 'auroc', 'aupr'], 
                 num_epochs=100, batch_size=128, shuffle=True, verbose=True, 
                 es_patience=10, es_metric='auroc', 
                 lr_decay=0.3, lr_patience=3, lr_metric='auroc'):

  # get validation data
  x_valid, y_valid = validation_data

  # create tensorflow dataset
  trainset = tf.data.Dataset.from_tensor_slices((x_train, y_train))

  # set up trainer
  trainer = Trainer(model)
  trainer.set_lr_decay(decay_rate=lr_decay, patience=lr_patience, metric=lr_metric)

  for epoch in range(num_epochs):  
    sys.stdout.write("\rEpoch %d \n"%(epoch+1))

    # train step
    train_loss, pred, y = trainer.train_epoch(trainset, shuffle=shuffle, 
                                              batch_size=batch_size, verbose=verbose)
    
    # validation performance
    trainer.evaluate('valid', x_valid, y_valid, verbose=1)

    # check learning rate decay      
    trainer.check_lr_decay(trainer.metrics.valid.value[lr_metric][-1])

    # check early stopping
    if trainer.early_stopping(es_metric, patience=es_patience):
      print("Patience ran out... Early stopping.")
      break
  
  return trainer



#------------------------------------------------------------------------------------------
# Trainer class
#------------------------------------------------------------------------------------------


class Trainer():
  def __init__(self, model):
    self.model = model

    metric_names = ['loss']
    for metric in model.metrics:
        metric_names.append(metric.name)
    self.metrics = TrainMetrics(metric_names)


  @tf.function
  def train_step(self, x, y):
    with tf.GradientTape() as tape:
      predictions = self.model(x, training=True)
      loss = self.model.loss(y, predictions)
      gradients = tape.gradient(loss, self.model.trainable_variables)
      
    # Update the weights of our linear layer.
    self.model.optimizer.apply_gradients(zip(gradients, self.model.trainable_weights))
    return loss, predictions


  def train_epoch(self, trainset, shuffle=True, batch_size=128, verbose=True):
    if shuffle:
      batch_dataset = trainset.shuffle(buffer_size=batch_size).batch(batch_size)
    num_batches = len(list(batch_dataset))
    
    running_loss = 0.
    pred_batch = []
    y_batch = []
    start_time = time.time()
    for i, (x, y) in enumerate(batch_dataset):      
      loss, pred = self.train_step(x, y)
      running_loss += loss
      pred_batch.append(pred)
      y_batch.append(y)

      if verbose:
        progress_bar(i+1, num_batches, start_time, bar_length=30, loss=running_loss/(i+1))
    pred = np.concatenate(pred_batch, axis=0)
    y = np.concatenate(y_batch, axis=0)

    return running_loss/(i+1), pred, y


  def predict(self, x, batch_size=128):
    pred = self.model.predict(x, batch_size=batch_size)  
    return pred


  def loss_predict(self, x, y, batch_size=128):
    pred = self.model.predict(x, batch_size=batch_size)  
    loss = self.model.loss(y, pred)
    return loss, pred

    
  def evaluate(self, name, x, y, batch_size=128, verbose=True):
    results = self.model.evaluate(x, y, batch_size=batch_size, verbose=0)
    metric_dic = {}
    for i, metric in enumerate(self.model.metrics):
        metric_dic[metric.name] = results[i+1]
    self.metrics.update(name, loss=results[0])
    self.metrics.update(name, **metric_dic)
    if verbose:
        self.metrics.print(name)


  def early_stopping(self, metric, patience):
    """check if validation loss is not improving and stop after patience
       runs out"""

    status = False
    vals = self.metrics.valid.value[metric]
    if metric == 'loss':
      index = np.argmin(vals)
    else:
      index = np.argmax(vals)
    if patience - (len(vals) - index - 1) <= 0:
      status = True
    return status


  def print_metrics(self, name):
    self.metrics.print(name)


  def set_lr_decay(self, decay_rate, patience, metric):
    self.lr_decay = LRDecay(optimizer=self.model.optimizer, decay_rate=decay_rate, 
                            patience=patience, metric=metric)


  def check_lr_decay(self, val):
    self.lr_decay.check(val)


#------------------------------------------------------------------------------------------
# Helper classes
#------------------------------------------------------------------------------------------


class LRDecay():
  def __init__(self, optimizer, decay_rate=0.3, patience=10, metric='loss'):

    self.optimizer = optimizer
    self.lr = optimizer.lr
    self.decay_rate = decay_rate
    self.patience = patience
    self.metric = metric
    self.index = 0
    self.initialize(metric)

  def initialize(self, metric):
    if metric == 'loss':
      self.best_val = 1e10
      self.sign = 1
    else:
      self.best_val = -1e10
      self.sign = -1

  def status(self, val):
    """check if validation loss is not improving and stop after patience
       runs out"""  
    status = False
    if self.sign*val < self.sign*self.best_val:
      self.best_val = val
      self.index = 0
    else:
      self.index += 1
      if self.index == self.patience:
        self.index = 0
        status = True
    return status

  def decay_learning_rate(self):
    self.lr = self.lr * self.decay_rate
    self.optimizer.learning_rate.assign(self.lr )

  def check(self, val):
    if self.status(val):
      self.decay_learning_rate()
      print('  Decaying learning rate to %.6f'%(self.lr))

      


#----------------------------------------------------------------------

class TrainMetrics():
  """wrapper class for monitoring training metrics"""
  def __init__(self, metric_names):
    self.train = MonitorMetrics(metric_names)
    self.valid = MonitorMetrics(metric_names)
    self.test = MonitorMetrics(metric_names)


  def update(self, name, **kwargs):
    if name == 'train':
      self.train.update(**kwargs)   
    elif name == 'valid':
      self.valid.update(**kwargs)  
    elif name == 'test':
      self.test.update(**kwargs) 

  def print(self, name):
    if name == 'train':
      self.train.print('train') 
    elif name == 'valid':
      self.valid.print('valid')  
    elif name == 'test':
      self.test.print('test') 

#----------------------------------------------------------------------

class MonitorMetrics():
  """class to monitor metrics during training"""
  def __init__(self, metric_names):
    self.value = {}
    self.metric_names = metric_names
    self.initialize_metrics(metric_names)
    
  def initialize_metrics(self, metric_names):
    """metric names can be list or dict"""
    self.value['loss'] = []
    if 'acc' in metric_names:
      self.value['acc'] = []
      self.value['acc_std'] = []
    if 'auroc' in metric_names:
      self.value['auroc'] = []
      self.value['auroc_std'] = []
    if 'aupr' in metric_names:
      self.value['aupr'] = []
      self.value['aupr_std'] = []
    if 'corr' in metric_names:
      self.value['corr'] = []
      self.value['corr_std'] = []
    if 'mcc' in metric_names:
      self.value['mcc'] = []
      self.value['mcc_std'] = []
    if 'mse' in metric_names:
      self.value['mse'] = []
      self.value['mse_std'] = []

  def update(self, **kwargs):    
    #  update metric dictionary
    for metric_name in kwargs.keys():
      self.value[metric_name].append(np.nanmean(kwargs[metric_name]))
      if metric_name != 'loss':
        self.value[metric_name+'_std'].append(np.nanstd(kwargs[metric_name]))

  def print(self, name):
    for metric_name in self.metric_names:
      if metric_name == 'loss':
        print("  " + name + " "+ metric_name+":\t{:.5f}".format(self.value[metric_name][-1]))
      else:
        print("  " + name + " "+ metric_name+":\t{:.5f}+/-{:.5f}"
                                    .format(self.value[metric_name][-1], 
                                            self.value[metric_name+'_std'][-1]))


#------------------------------------------------------------------------------
# Useful functions
#------------------------------------------------------------------------------


def progress_bar(iter, num_batches, start_time, bar_length=30, **kwargs):
  """plots a progress bar to show remaining time for a full epoch. 
     (inspired by keras)"""

  # calculate progress bar 
  percent = iter/num_batches
  progress = '='*int(round(percent*bar_length))
  spaces = ' '*int(bar_length-round(percent*bar_length))

  # setup text to output
  if iter == num_batches:   # if last batch, then output total elapsed time
    output_text = "\r[%s] %.1f%% -- elapsed time=%.1fs"
    elapsed_time = time.time()-start_time
    output_vals = [progress+spaces, percent*100, elapsed_time]
  else:
    output_text = "\r[%s] %.1f%%  -- remaining time=%.1fs"
    remaining_time = (time.time()-start_time)*(num_batches-(iter+1))/(iter+1)
    output_vals = [progress+spaces, percent*100, remaining_time]

  # add performance metrics if included in kwargs
  if 'loss' in kwargs:
    output_text += " -- loss=%.5f"
    output_vals.append(kwargs['loss'])
  if 'acc' in kwargs:
    output_text += " -- acc=%.5f"
    output_vals.append(kwargs['acc'])
  if 'auroc' in kwargs:
    output_text += " -- auroc=%.5f"
    output_vals.append(kwargs['auroc'])
  if 'aupr' in kwargs:
    output_text += " -- aupr=%.5f"
    output_vals.append(kwargs['aupr'])
  if 'pearsonr' in kwargs:
    output_text += " -- pearsonr=%.5f"
    output_vals.append(kwargs['pearsonr'])
  if 'mcc' in kwargs:
    output_text += " -- mcc=%.5f"
    output_vals.append(kwargs['mcc'])
  if 'mse' in kwargs:
    output_text += " -- mse=%.5f"
    output_vals.append(kwargs['mse'])

  # set new line when finished
  if iter == num_batches:
    output_text += "\n"
  
  # output stats
  sys.stdout.write(output_text%tuple(output_vals))
   


