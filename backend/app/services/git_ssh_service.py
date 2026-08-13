"""
Git SSH Service - Generate SSH keys and access Git repositories via SSH
"""

import os
import sys
import re
import shlex
import logging
import tempfile
import subprocess
import shutil
import hashlib
import base64
from typing import Tuple, Optional, Dict, List
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


def is_valid_branch_name(branch: str) -> bool:
    if not branch:
        return False

    if not re.match(r'^[\w\-/.]+$', branch):
        return False

    if branch.startswith('.') or branch.startswith('-'):
        return False

    if branch.endswith('/'):
        return False

    if branch.endswith('.lock'):
        return False

    if '..' in branch:
        return False

    if '//' in branch:
        return False

    return True


def get_ssh_config_dir() -> str:
    from app.core.config import settings

    ssh_config_path = Path(settings.SSH_CONFIG_PATH)

    ssh_config_path.mkdir(parents=True, exist_ok=True)

    if sys.platform != 'win32':
        os.chmod(ssh_config_path, 0o700)

    return str(ssh_config_path.absolute())


def get_known_hosts_file() -> str:
    ssh_config_dir = get_ssh_config_dir()
    known_hosts_file = Path(ssh_config_dir) / 'known_hosts'

    if not known_hosts_file.exists():
        known_hosts_file.touch()
        if sys.platform != 'win32':
            os.chmod(known_hosts_file, 0o600)

    return str(known_hosts_file.absolute())


def clear_known_hosts() -> bool:
    try:
        known_hosts_file = get_known_hosts_file()
        with open(known_hosts_file, 'w') as f:
            f.write('')
        logger.info(f"Cleared known_hosts file: {known_hosts_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to clear known_hosts: {e}")
        return False


def set_secure_file_permissions(file_path: str):
    if sys.platform == 'win32':
        try:
            subprocess.run(
                ['icacls', file_path, '/inheritance:r'],
                capture_output=True,
                check=True
            )
            subprocess.run(
                ['icacls', file_path, '/grant:r', f'{os.environ.get("USERNAME")}:(F)'],
                capture_output=True,
                check=True
            )
        except Exception as e:
            logger.warning(f"Failed to set Windows file permissions: {e}")
            try:
                os.chmod(file_path, 0o600)
            except OSError:
                # L2: chmod 常见失败是 OSError；其他异常上抛
                pass
    else:
        os.chmod(file_path, 0o600)


class SSHKeyService:
    """SSH Key Service"""

    @staticmethod
    def get_public_key_fingerprint(public_key: str) -> Optional[str]:
        try:
            parts = public_key.strip().split()
            if len(parts) < 2:
                return None

            key_data = parts[1]
            key_bytes = base64.b64decode(key_data)
            sha256_hash = hashlib.sha256(key_bytes).digest()
            fingerprint = base64.b64encode(sha256_hash).decode('utf-8').rstrip('=')

            return f"SHA256:{fingerprint}"

        except Exception as e:
            logger.error(f"Fingerprint calculation error: {e}")
            return None

    @staticmethod
    def verify_key_pair(private_key: str, public_key: str) -> bool:
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_ssh_private_key,
                load_pem_private_key
            )
            from cryptography.hazmat.backends import default_backend

            private_key_bytes = private_key.encode('utf-8')
            private_key_obj = None

            try:
                private_key_obj = load_ssh_private_key(
                    private_key_bytes,
                    password=None,
                    backend=default_backend()
                )
            except Exception:
                try:
                    private_key_obj = load_pem_private_key(
                        private_key_bytes,
                        password=None,
                        backend=default_backend()
                    )
                except Exception as e:
                    logger.debug(f"Failed to load private key: {e}")
                    return False

            if not private_key_obj:
                return False

            derived_public_key = private_key_obj.public_key()
            derived_public_bytes = derived_public_key.public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH
            ).decode('utf-8').strip()

            expected_public = public_key.split()[0] + ' ' + public_key.split()[1]
            actual_public = derived_public_bytes.split()[0] + ' ' + derived_public_bytes.split()[1]

            return expected_public == actual_public

        except Exception as e:
            logger.error(f"Key verification error: {e}")
            return False

    @staticmethod
    def generate_rsa_key(key_size: int = 4096) -> Tuple[str, str]:
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ).decode('utf-8')

        public_key = private_key.public_key()
        public_openssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        ).decode('utf-8')

        return private_pem, public_openssh

    @staticmethod
    def generate_ed25519_key() -> Tuple[str, str]:
        private_key = ed25519.Ed25519PrivateKey.generate()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption()
        ).decode('utf-8')

        public_key = private_key.public_key()
        public_openssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        ).decode('utf-8')

        return private_pem, public_openssh


class GitSSHOperations:
    """Git SSH Operations - Clone and pull repositories using SSH keys"""

    @staticmethod
    def is_ssh_url(url: str) -> bool:
        return url.startswith('git@') or url.startswith('ssh://')

    @staticmethod
    def clone_repo_with_ssh(repo_url: str, private_key: str, target_dir: str, branch: str = None) -> Dict[str, any]:
        from app.core.config import settings

        temp_dir = None
        try:
            if branch and not is_valid_branch_name(branch):
                logger.warning(f"Invalid branch name rejected: {branch}")
                return {'success': False, 'message': f'Invalid branch name: {branch}'}

            temp_dir = tempfile.mkdtemp(prefix='lanjian_ssh_')
            key_file = os.path.join(temp_dir, 'id_rsa')

            with open(key_file, 'w') as f:
                f.write(private_key)
            set_secure_file_permissions(key_file)

            known_hosts_file = get_known_hosts_file()

            env = os.environ.copy()

            ssh_cmd = (
                f"ssh -i {shlex.quote(key_file)} "
                f"-o StrictHostKeyChecking=accept-new "
                f"-o UserKnownHostsFile={shlex.quote(known_hosts_file)} "
                f"-o PreferredAuthentications=publickey "
                f"-o IdentitiesOnly=yes"
            )

            env['GIT_SSH_COMMAND'] = ssh_cmd
            logger.debug(f"Using SSH key file: {key_file}")
            logger.debug(f"Using known_hosts file: {known_hosts_file}")

            cmd = ['git', 'clone', '--depth', '1']
            if branch:
                cmd.extend(['--branch', branch])
            cmd.extend([repo_url, target_dir])

            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=settings.SSH_CLONE_TIMEOUT
            )

            if result.returncode == 0:
                return {
                    'success': True,
                    'message': 'Repository cloned successfully',
                    'path': target_dir
                }
            else:
                logger.error(f"Git clone failed: {result.stderr}")
                return {
                    'success': False,
                    'message': 'Repository clone failed',
                    'error': result.stderr
                }

        except subprocess.TimeoutExpired:
            logger.error(f"Git clone timeout after {settings.SSH_CLONE_TIMEOUT}s")
            return {'success': False, 'message': f'Clone timeout (exceeded {settings.SSH_CLONE_TIMEOUT}s)'}
        except Exception as e:
            logger.error(f"Git clone error: {e}")
            return {'success': False, 'message': f'Clone failed: {str(e)}'}
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def get_repo_files_via_ssh(repo_url: str, private_key: str, branch: str = "main",
                                exclude_patterns: List[str] = None) -> List[Dict[str, str]]:
        temp_clone_dir = None
        try:
            temp_clone_dir = tempfile.mkdtemp(prefix='lanjian_clone_')

            clone_result = GitSSHOperations.clone_repo_with_ssh(
                repo_url, private_key, temp_clone_dir, branch
            )

            if not clone_result['success']:
                raise Exception(f"Clone failed: {clone_result.get('error', '')}")

            from app.services.scanner import is_text_file, should_exclude

            files = []
            for root, dirs, filenames in os.walk(temp_clone_dir):
                if '.git' in dirs:
                    dirs.remove('.git')

                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(file_path, temp_clone_dir)

                    if should_exclude(rel_path, exclude_patterns):
                        continue

                    if not is_text_file(rel_path):
                        continue

                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()

                        files.append({
                            'path': rel_path.replace('\\', '/'),
                            'content': content
                        })
                    except Exception as e:
                        logger.debug(f"Failed to read file {rel_path}: {e}")
                        continue

            return files

        except Exception as e:
            logger.error(f"Failed to get SSH repo files: {e}")
            raise
        finally:
            if temp_clone_dir and os.path.exists(temp_clone_dir):
                shutil.rmtree(temp_clone_dir, ignore_errors=True)

    @staticmethod
    def test_ssh_key(repo_url: str, private_key: str) -> Dict[str, any]:
        from app.core.config import settings

        temp_dir = None
        try:
            if '@' in repo_url:
                host_part = repo_url.split('@')[1].split(':')[0]
            else:
                return {'success': False, 'message': 'Invalid URL format'}

            if not re.match(r'^[\w\-\.]+$', host_part):
                logger.warning(f"Invalid host name rejected: {host_part}")
                return {'success': False, 'message': 'Invalid host name'}

            temp_dir = tempfile.mkdtemp(prefix='lanjian_ssh_test_')
            key_file = os.path.join(temp_dir, 'id_rsa')

            with open(key_file, 'w') as f:
                f.write(private_key)

            if not os.path.exists(key_file):
                return {'success': False, 'message': 'Failed to create key file'}

            set_secure_file_permissions(key_file)

            known_hosts_file = get_known_hosts_file()

            cmd = [
                'ssh',
                '-i', key_file,
                '-o', 'StrictHostKeyChecking=accept-new',
                '-o', f'UserKnownHostsFile={known_hosts_file}',
                '-o', f'ConnectTimeout={settings.SSH_CONNECT_TIMEOUT}',
                '-o', 'PreferredAuthentications=publickey',
                '-o', 'IdentitiesOnly=yes',
                '-v',
                '-T', f'git@{host_part}'
            ]

            logger.debug(f"Testing SSH connection to: {host_part}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.SSH_TEST_TIMEOUT
            )

            output = result.stdout + result.stderr
            output_lower = output.lower()

            if 'anonymous' in output_lower:
                return {
                    'success': True,
                    'message': 'SSH connection successful, but public key not linked to account',
                    'output': 'Note: Server shows Anonymous. This is normal when deploying keys. Please add the SSH public key in your Git service settings.'
                }

            success_indicators = [
                ('successfully authenticated', True),
                ('hi ', True),
                ('welcome to gitlab', '@' in output),
                ('welcome to codeup', '@' in output),
            ]

            is_success = False
            for indicator, extra_check in success_indicators:
                if indicator in output_lower:
                    if extra_check is True or extra_check:
                        is_success = True
                        break

            if is_success:
                return {
                    'success': True,
                    'message': 'SSH key verification successful',
                    'output': output
                }
            else:
                error_msg = 'SSH key verification failed'
                if 'permission denied' in output_lower:
                    error_msg = 'SSH key verification failed: Permission denied. Please confirm the public key has been added to the Git service.'
                elif 'connection refused' in output_lower:
                    error_msg = 'SSH connection refused. Please check network connectivity.'
                elif 'no route to host' in output_lower:
                    error_msg = 'SSH connection failed: Cannot reach host.'
                elif not output.strip():
                    error_msg = 'SSH connection failed: No response received.'

                return {
                    'success': False,
                    'message': error_msg,
                    'output': output if output.strip() else 'No response received.'
                }

        except subprocess.TimeoutExpired:
            logger.warning(f"SSH test timeout after {settings.SSH_TEST_TIMEOUT}s")
            return {
                'success': False,
                'message': f'SSH connection timeout ({settings.SSH_TEST_TIMEOUT}s)',
                'output': 'Connection timeout. Please check network or Git service availability.'
            }
        except Exception as e:
            logger.error(f"SSH test error: {e}")
            return {
                'success': False,
                'message': 'Test failed, please try again later',
                'output': ''
            }
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
