"""
Security Controls Configuration Loader (v3.1 Fusion)

浠?code-audit-main 杩佺Щ鐨?YAML 閰嶇疆鍔犺浇鍣? 閫傞厤 lanjian 寮傛鐜銆?
鍔犺浇瀹夊叏鎺у埗鐭╅樀鍜岃瑷€閫傞厤鍣紝鎸夐渶缂撳瓨銆?
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class SecurityControl:
    """瀹夊叏鎺у埗瀹氫箟"""
    id: str
    name: str
    name_zh: str
    description: str
    severity: str
    cwe: str


@dataclass
class SensitiveOperation:
    """鏁忔劅鎿嶄綔瀹氫箟"""
    name: str
    name_zh: str
    patterns: List[str]
    required_controls: List[str]
    risk_level: str
    description: str


class SecurityControlsConfigLoader:
    """
    YAML 閰嶇疆鍔犺浇鍣?

    浠?code-audit-main 鐨?security_controls_matrix.yaml 鍜?adapters/*.yaml
    鍔犺浇瀹夊叏鎺у埗瀹氫箟鍜岃瑷€鐗瑰畾妫€娴嬫ā寮忋€?
    鏀寔鍐呭瓨缂撳瓨閬垮厤閲嶅 I/O銆?
    """

    def __init__(self, config_dir: str = None):
        if config_dir is None:
            config_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data"
            )
        self.config_dir = Path(config_dir)
        self._matrix_cache: Optional[Dict] = None
        self._adapter_cache: Dict[str, Dict] = {}

    @property
    def matrix_path(self) -> Path:
        return self.config_dir / "security_controls_matrix.yaml"

    def adapter_path(self, language: str) -> Path:
        return self.config_dir / "adapters" / f"{language}.yaml"

    # ---- Matrix loading ----

    def load_matrix(self) -> Dict:
        """鍔犺浇瀹夊叏鎺у埗鐭╅樀锛堝甫缂撳瓨锛?""
        if self._matrix_cache is not None:
            return self._matrix_cache
        if not self.matrix_path.exists():
            raise FileNotFoundError(
                f"Security controls matrix not found: {self.matrix_path}"
            )
        with open(self.matrix_path, 'r', encoding='utf-8') as f:
            self._matrix_cache = yaml.safe_load(f)
        return self._matrix_cache

    def get_security_controls(self) -> Dict[str, SecurityControl]:
        """鑾峰彇鎵€鏈夊畨鍏ㄦ帶鍒跺畾涔?""
        matrix = self.load_matrix()
        controls = {}
        for ctrl_id, ctrl_data in matrix.get('security_controls', {}).items():
            controls[ctrl_id] = SecurityControl(
                id=ctrl_data.get('id', ctrl_id.upper()),
                name=ctrl_data.get('name', ctrl_id),
                name_zh=ctrl_data.get('name_zh', ctrl_id),
                description=ctrl_data.get('description', ''),
                severity=ctrl_data.get('severity', 'MEDIUM'),
                cwe=ctrl_data.get('cwe', 'CWE-000'),
            )
        return controls

    def get_sensitive_operations(self) -> Dict[str, SensitiveOperation]:
        """鑾峰彇鎵€鏈夋晱鎰熸搷浣滃畾涔?""
        matrix = self.load_matrix()
        operations = {}
        for op_name, op_data in matrix.get('sensitive_operations', {}).items():
            operations[op_name] = SensitiveOperation(
                name=op_data.get('name', op_name),
                name_zh=op_data.get('name_zh', op_name),
                patterns=op_data.get('patterns', []),
                required_controls=op_data.get('required_controls', []),
                risk_level=op_data.get('risk_level', 'MEDIUM'),
                description=op_data.get('description', ''),
            )
        return operations

    # ---- Adapter loading ----

    def load_adapter(self, language: str) -> Dict:
        """鍔犺浇璇█閫傞厤鍣紙甯︾紦瀛橈級"""
        if language in self._adapter_cache:
            return self._adapter_cache[language]
        adapter_file = self.adapter_path(language)
        if not adapter_file.exists():
            raise FileNotFoundError(
                f"Adapter not found for language '{language}': {adapter_file}"
            )
        with open(adapter_file, 'r', encoding='utf-8') as f:
            adapter = yaml.safe_load(f)
            self._adapter_cache[language] = adapter
        return adapter

    def get_available_languages(self) -> List[str]:
        """鑾峰彇鎵€鏈夊彲鐢ㄧ殑璇█閫傞厤鍣?""
        adapters_dir = self.config_dir / "adapters"
        if not adapters_dir.exists():
            return []
        return [
            f.stem for f in adapters_dir.glob("*.yaml")
        ]

    def get_file_extensions(self, language: str) -> List[str]:
        """鑾峰彇璇█瀵瑰簲鐨勬枃浠舵墿灞曞悕"""
        adapter = self.load_adapter(language)
        return adapter.get('file_extensions', [])

    def get_control_patterns(self, language: str) -> Dict:
        """鑾峰彇璇█鐨勫畨鍏ㄦ帶鍒舵娴嬫ā寮?""
        adapter = self.load_adapter(language)
        return adapter.get('control_patterns', {})

    def get_operation_patterns(self, language: str) -> Dict:
        """鑾峰彇璇█鐨勬晱鎰熸搷浣滆瘑鍒ā寮?""
        adapter = self.load_adapter(language)
        return adapter.get('operation_patterns', {})

    def get_frameworks(self, language: str) -> Dict:
        """鑾峰彇璇█鐨勬鏋剁壒瀹氶厤缃?""
        adapter = self.load_adapter(language)
        return adapter.get('frameworks', {})

    # ---- Cache management ----

    def clear_cache(self):
        """娓呴櫎鎵€鏈夌紦瀛?""
        self._matrix_cache = None
        self._adapter_cache.clear()


# 鍏ㄥ眬鍗曚緥锛堟噿鍔犺浇锛?
_loader_instance: Optional[SecurityControlsConfigLoader] = None


def get_controls_loader() -> SecurityControlsConfigLoader:
    """鑾峰彇鍏ㄥ眬閰嶇疆鍔犺浇鍣ㄥ崟渚?""
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = SecurityControlsConfigLoader()
    return _loader_instance
