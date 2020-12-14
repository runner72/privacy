# Copyright 2020, The TensorFlow Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Code that runs membership inference attacks based on the model outputs.

This file belongs to the new API for membership inference attacks. This file
will be renamed to membership_inference_attack.py after the old API is removed.
"""

from typing import Iterable
import numpy as np
from sklearn import metrics

from tensorflow_privacy.privacy.membership_inference_attack import models
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import AttackInputData
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import AttackResults
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import AttackType
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import \
  PrivacyReportMetadata
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import RocCurve
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import SingleAttackResult
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import SingleSliceSpec
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import SlicingSpec
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import SingleRiskScoreResult
from tensorflow_privacy.privacy.membership_inference_attack.data_structures import RiskScoreResults
from tensorflow_privacy.privacy.membership_inference_attack.dataset_slicing import get_single_slice_specs
from tensorflow_privacy.privacy.membership_inference_attack.dataset_slicing import get_slice


def _get_slice_spec(data: AttackInputData) -> SingleSliceSpec:
  if hasattr(data, 'slice_spec'):
    return data.slice_spec
  return SingleSliceSpec()


def _run_trained_attack(attack_input: AttackInputData,
                        attack_type: AttackType,
                        balance_attacker_training: bool = True):
  """Classification attack done by ML models."""
  attacker = None

  if attack_type == AttackType.LOGISTIC_REGRESSION:
    attacker = models.LogisticRegressionAttacker()
  elif attack_type == AttackType.MULTI_LAYERED_PERCEPTRON:
    attacker = models.MultilayerPerceptronAttacker()
  elif attack_type == AttackType.RANDOM_FOREST:
    attacker = models.RandomForestAttacker()
  elif attack_type == AttackType.K_NEAREST_NEIGHBORS:
    attacker = models.KNearestNeighborsAttacker()
  else:
    raise NotImplementedError('Attack type %s not implemented yet.' %
                              attack_type)

  prepared_attacker_data = models.create_attacker_data(
      attack_input, balance=balance_attacker_training)

  attacker.train_model(prepared_attacker_data.features_train,
                       prepared_attacker_data.is_training_labels_train)

  # Run the attacker on (permuted) test examples.
  predictions_test = attacker.predict(prepared_attacker_data.features_test)

  # Generate ROC curves with predictions.
  fpr, tpr, thresholds = metrics.roc_curve(
      prepared_attacker_data.is_training_labels_test, predictions_test)

  roc_curve = RocCurve(tpr=tpr, fpr=fpr, thresholds=thresholds)

  return SingleAttackResult(
      slice_spec=_get_slice_spec(attack_input),
      attack_type=attack_type,
      roc_curve=roc_curve)


def _run_threshold_attack(attack_input: AttackInputData):
  fpr, tpr, thresholds = metrics.roc_curve(
      np.concatenate((np.zeros(attack_input.get_train_size()),
                      np.ones(attack_input.get_test_size()))),
      np.concatenate(
          (attack_input.get_loss_train(), attack_input.get_loss_test())))

  roc_curve = RocCurve(tpr=tpr, fpr=fpr, thresholds=thresholds)

  return SingleAttackResult(
      slice_spec=_get_slice_spec(attack_input),
      attack_type=AttackType.THRESHOLD_ATTACK,
      roc_curve=roc_curve)


def _run_threshold_entropy_attack(attack_input: AttackInputData):
  fpr, tpr, thresholds = metrics.roc_curve(
      np.concatenate((np.zeros(attack_input.get_train_size()),
                      np.ones(attack_input.get_test_size()))),
      np.concatenate(
          (attack_input.get_entropy_train(), attack_input.get_entropy_test())))

  roc_curve = RocCurve(tpr=tpr, fpr=fpr, thresholds=thresholds)

  return SingleAttackResult(
      slice_spec=_get_slice_spec(attack_input),
      attack_type=AttackType.THRESHOLD_ENTROPY_ATTACK,
      roc_curve=roc_curve)


def _run_attack(attack_input: AttackInputData,
                attack_type: AttackType,
                balance_attacker_training: bool = True):
  attack_input.validate()
  if attack_type.is_trained_attack:
    return _run_trained_attack(attack_input, attack_type,
                               balance_attacker_training)
  if attack_type == AttackType.THRESHOLD_ENTROPY_ATTACK:
    return _run_threshold_entropy_attack(attack_input)
  return _run_threshold_attack(attack_input)


def run_attacks(attack_input: AttackInputData,
                slicing_spec: SlicingSpec = None,
                attack_types: Iterable[AttackType] = (
                    AttackType.THRESHOLD_ATTACK,),
                privacy_report_metadata: PrivacyReportMetadata = None,
                balance_attacker_training: bool = True) -> AttackResults:
  """Runs membership inference attacks on a classification model.

  It runs attacks specified by attack_types on each attack_input slice which is
   specified by slicing_spec.

  Args:
    attack_input: input data for running an attack
    slicing_spec: specifies attack_input slices to run attack on
    attack_types: attacks to run
    privacy_report_metadata: the metadata of the model under attack.
    balance_attacker_training: Whether the training and test sets for the
          membership inference attacker should have a balanced (roughly equal)
          number of samples from the training and test sets used to develop
          the model under attack.

  Returns:
    the attack result.
  """
  attack_input.validate()
  attack_results = []

  if slicing_spec is None:
    slicing_spec = SlicingSpec(entire_dataset=True)
  num_classes = None
  if slicing_spec.by_class:
    num_classes = attack_input.num_classes
  input_slice_specs = get_single_slice_specs(slicing_spec, num_classes)
  for single_slice_spec in input_slice_specs:
    attack_input_slice = get_slice(attack_input, single_slice_spec)
    for attack_type in attack_types:
      attack_results.append(
          _run_attack(attack_input_slice, attack_type,
                      balance_attacker_training))

  privacy_report_metadata = _compute_missing_privacy_report_metadata(
      privacy_report_metadata, attack_input)

  return AttackResults(
      single_attack_results=attack_results,
      privacy_report_metadata=privacy_report_metadata)


def _compute_privacy_risk_score(attack_input: AttackInputData,
                                num_bins: int = 15) -> SingleRiskScoreResult:
  """compute each individual point's likelihood of being a member (https://arxiv.org/abs/2003.10595)
  Args:
    attack_input: input data for compute privacy risk scores
    num_bins: the number of bins used to compute the training/test histogram; we set the default as 15
  
  Returns:
    privacy risk score results
  """
  
  # If the loss or the entropy is provided, just use it; 
  # Otherwise, call the function to compute the loss (you can also choose to compute entropy) 
  if attack_input.loss_train is not None and attack_input.loss_test is not None:
    train_values, test_values = attack_input.loss_train, attack_input.loss_test
  elif attack_input.entropy_train is not None and attack_input.entropy_test is not None:
    train_values, test_values = attack_input.entropy_train, attack_input.entropy_test
  else:
    train_values, test_values = attack_input.get_loss_train(), attack_input.get_loss_test()
  
  # Compute the histogram in the log scale
  small_value = 1e-10
  train_log_values = np.log(np.maximum(train_values, small_value))
  test_log_values = np.log(np.maximum(test_values, small_value))
  
  min_log_value = np.amin(np.concatenate((train_log_values, test_log_values)))
  max_log_value = np.amax(np.concatenate((train_log_values, test_log_values)))
  bins_hist = np.linspace(min_log_value, max_log_value, num_bins+1)
  
  train_hist, _ = np.histogram(train_log_values, bins=bins_hist)
  train_hist = train_hist/(len(train_log_values)+0.0)
  train_hist_indices = np.fmin(np.digitize(train_log_values, bins=bins_hist),num_bins)-1
  
  test_hist, _ = np.histogram(test_log_values, bins=bins_hist)
  test_hist = test_hist/(len(test_log_values)+0.0)
  test_hist_indices = np.fmin(np.digitize(test_log_values, bins=bins_hist),num_bins)-1
  
  combined_hist = train_hist+test_hist
  combined_hist[combined_hist==0] = small_value
  privacy_risk_list = train_hist/(combined_hist+0.0)
  train_risk_scores = privacy_risk_list[train_hist_indices]
  test_risk_scores = privacy_risk_list[test_hist_indices]
  
  
  
  return SingleRiskScoreResult(slice_spec=_get_slice_spec(attack_input),
                               train_risk_scores=train_risk_scores,
                               test_risk_scores=test_risk_scores)


def privacy_risk_score_analysis(attack_input: AttackInputData,
                                slicing_spec: SlicingSpec = None) -> RiskScoreResults:
    
  """Perform privacy risk score analysis on all given slice types

  Args:
    attack_input: input data for compute privacy risk scores
    slicing_spec: specifies attack_input slices

  Returns:
    the privacy risk score results.
  """
  attack_input.validate()
  risk_score_results = []

  if slicing_spec is None:
    slicing_spec = SlicingSpec(entire_dataset=True)
  num_classes = None
  if slicing_spec.by_class:
    num_classes = attack_input.num_classes
  input_slice_specs = get_single_slice_specs(slicing_spec, num_classes)
  for single_slice_spec in input_slice_specs:
    attack_input_slice = get_slice(attack_input, single_slice_spec)
    risk_score_results.append(_compute_privacy_risk_score(attack_input_slice))
    
  return RiskScoreResults(risk_score_results=risk_score_results)



def _compute_missing_privacy_report_metadata(
    metadata: PrivacyReportMetadata,
    attack_input: AttackInputData) -> PrivacyReportMetadata:
  """Populates metadata fields if they are missing."""
  if metadata is None:
    metadata = PrivacyReportMetadata()
  if metadata.accuracy_train is None:
    metadata.accuracy_train = _get_accuracy(attack_input.logits_train,
                                            attack_input.labels_train)
  if metadata.accuracy_test is None:
    metadata.accuracy_test = _get_accuracy(attack_input.logits_test,
                                           attack_input.labels_test)
  if metadata.loss_train is None:
    metadata.loss_train = np.average(attack_input.get_loss_train())
  if metadata.loss_test is None:
    metadata.loss_test = np.average(attack_input.get_loss_test())
  return metadata


def _get_accuracy(logits, labels):
  """Computes the accuracy if it is missing."""
  if logits is None or labels is None:
    return None
  return metrics.accuracy_score(labels, np.argmax(logits, axis=1))
