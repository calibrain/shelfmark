export class UserCancelledError extends Error {
  constructor(message: string = 'Cancelled') {
    super(message);
    this.name = 'UserCancelledError';
  }
}

export function isUserCancelledError(error: unknown): error is UserCancelledError {
  return error instanceof UserCancelledError;
}

