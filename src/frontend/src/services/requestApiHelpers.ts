export interface FulfilAdminRequestBody {
  release_data: Record<string, unknown>;
  admin_note?: string;
}

export interface RejectAdminRequestBody {
  admin_note?: string;
}

export const buildFulfilBookRequestsUrl = (adminRequestsBaseUrl: string, bookId: number): string =>
  `${adminRequestsBaseUrl}/books/${encodeURIComponent(String(bookId))}/fulfil`;

export const buildFulfilAdminRequestBody = (
  body: FulfilAdminRequestBody,
): FulfilAdminRequestBody => {
  const payload: FulfilAdminRequestBody = { release_data: body.release_data };
  if (body.admin_note !== undefined) {
    payload.admin_note = body.admin_note;
  }
  return payload;
};

export const buildRejectAdminRequestBody = (
  body: RejectAdminRequestBody = {},
): RejectAdminRequestBody => {
  return {
    admin_note: body.admin_note,
  };
};
