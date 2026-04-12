class UserCancelledError extends Error {
  constructor(message: string = 'Cancelled') {
    super(message);
    this.name = 'UserCancelledError';
  }
}

export function isUserCancelledError(error: unknown): boolean {
  return error instanceof UserCancelledError;
}
