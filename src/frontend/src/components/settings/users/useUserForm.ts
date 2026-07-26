import { useState } from 'react';

import type { AdminUser } from '../../../services/api';
import { INITIAL_CREATE_FORM } from './types';

export const useUserForm = () => {
  const [createForm, setCreateForm] = useState({ ...INITIAL_CREATE_FORM });
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const resetCreateForm = () => setCreateForm({ ...INITIAL_CREATE_FORM });
  const beginEditing = (user: AdminUser) => {
    setEditingUser({ ...user });
    setEditPassword('');
    setEditPasswordConfirm('');
  };
  const clearEditState = () => {
    setEditingUser(null);
    setEditPassword('');
    setEditPasswordConfirm('');
  };
  return {
    createForm,
    setCreateForm,
    resetCreateForm,
    editingUser,
    setEditingUser,
    beginEditing,
    clearEditState,
    editPassword,
    setEditPassword,
    editPasswordConfirm,
    setEditPasswordConfirm,
  };
};
