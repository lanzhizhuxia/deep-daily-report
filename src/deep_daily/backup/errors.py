from __future__ import annotations


class BackupError(RuntimeError):
    exit_code = 1


class BackupConfigError(BackupError):
    exit_code = 1


class BackupValidationError(BackupError):
    exit_code = 1


class BackupNetworkError(BackupError):
    exit_code = 2


class BackupChecksumMismatchError(BackupError):
    exit_code = 3


class BackupLockHeldError(BackupError):
    exit_code = 4


class BackupLockRecoveryRefusedError(BackupError):
    exit_code = 5
