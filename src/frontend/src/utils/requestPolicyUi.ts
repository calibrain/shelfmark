import { ButtonStateInfo, RequestPolicyMode } from '../types';

export const applyDirectPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  if (mode === 'request_release' || mode === 'request_book') {
    return { text: 'Request', state: 'download' };
  }

  return baseState;
};

export const applyUniversalPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'request_book') {
    return { text: 'Request', state: 'download' };
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  return { ...baseState, text: 'Get' };
};

