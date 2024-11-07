from collections import defaultdict
from functools import cached_property
from typing import AbstractSet, Mapping, Set

from dagster import AssetKey, AssetSpec, Definitions
from dagster._annotations import public
from dagster._record import record

from dagster_airlift.core.airflow_instance import AirflowInstance
from dagster_airlift.core.serialization.compute import (
    AirliftMetadataMappingInfo,
    build_airlift_metadata_mapping_info,
)
from dagster_airlift.core.serialization.serialized_data import DagHandle, TaskHandle
from dagster_airlift.core.utils import (
    dag_handles_for_spec,
    is_dag_mapped_asset_spec,
    is_peered_dag_asset_spec,
    is_task_mapped_asset_spec,
    peered_dag_handles_for_spec,
    task_handles_for_spec,
)


@record
class AirflowDefinitionsData:
    """A class that holds data about the assets that are mapped to Airflow dags and tasks, and
    provides methods for retrieving information about the mappings.
    The user should not instantiate this class directly. It is provided when customizing the events
    that are generated by the Airflow sensor using the `event_transformer_fn` argument of
    :py:func:`build_defs_from_airflow_instance`.
    """

    airflow_instance: AirflowInstance
    mapped_defs: Definitions

    @public
    @property
    def instance_name(self) -> str:
        """The name of the Airflow instance."""
        return self.airflow_instance.name

    @cached_property
    def mapping_info(self) -> AirliftMetadataMappingInfo:
        return build_airlift_metadata_mapping_info(self.mapped_defs)

    @cached_property
    def all_asset_specs_by_key(self) -> Mapping[AssetKey, AssetSpec]:
        return {spec.key: spec for spec in self.mapped_defs.get_all_asset_specs()}

    @public
    def task_ids_in_dag(self, dag_id: str) -> Set[str]:
        """Returns the task ids within the given dag_id.

        Args:
            dag_id (str): The dag id.
        """
        return self.mapping_info.task_id_map[dag_id]

    @property
    def dag_ids_with_mapped_asset_keys(self) -> AbstractSet[str]:
        return self.mapping_info.dag_ids

    @cached_property
    def mapped_asset_keys_by_task_handle(self) -> Mapping[TaskHandle, AbstractSet[AssetKey]]:
        asset_keys_per_handle = defaultdict(set)
        for spec in self.mapped_defs.get_all_asset_specs():
            if is_task_mapped_asset_spec(spec):
                task_handles = task_handles_for_spec(spec)
                for task_handle in task_handles:
                    asset_keys_per_handle[task_handle].add(spec.key)
        return asset_keys_per_handle

    @cached_property
    def mapped_asset_keys_by_dag_handle(self) -> Mapping[DagHandle, AbstractSet[AssetKey]]:
        asset_keys_per_handle = defaultdict(set)
        for spec in self.mapped_defs.get_all_asset_specs():
            if is_dag_mapped_asset_spec(spec):
                dag_handles = dag_handles_for_spec(spec)
                for dag_handle in dag_handles:
                    asset_keys_per_handle[dag_handle].add(spec.key)
        return asset_keys_per_handle

    @cached_property
    def peered_dag_asset_keys_by_dag_handle(self) -> Mapping[DagHandle, AbstractSet[AssetKey]]:
        asset_keys_per_handle = defaultdict(set)
        for spec in self.mapped_defs.get_all_asset_specs():
            if is_peered_dag_asset_spec(spec):
                dag_handles = peered_dag_handles_for_spec(spec)
                for dag_handle in dag_handles:
                    asset_keys_per_handle[dag_handle].add(spec.key)
        return asset_keys_per_handle

    @public
    def asset_keys_in_task(self, dag_id: str, task_id: str) -> AbstractSet[AssetKey]:
        """Returns the asset keys that are mapped to the given task.

        Args:
            dag_id (str): The dag id.
            task_id (str): The task id.
        """
        return self.mapped_asset_keys_by_task_handle[TaskHandle(dag_id=dag_id, task_id=task_id)]
