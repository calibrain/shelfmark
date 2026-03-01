import type { ActingAsUserSelection } from '../types';

export const formatActingAsUserName = (user: ActingAsUserSelection): string => {
  return user.displayName || user.username;
};
