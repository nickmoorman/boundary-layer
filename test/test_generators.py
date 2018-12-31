import copy
import re
import yaml

from boundary_layer import plugins
from boundary_layer.builders import PrimaryDagBuilder
from boundary_layer.containers import ExecutionContext
from boundary_layer.registry import NodeTypes
from boundary_layer.registry.types.generator import GeneratorNode
from boundary_layer.schemas.internal.generators import GeneratorSpecSchema


def test_default_param_filler():
    generator_config = {
        'name': 'do_snapshot_copiers',
        'type': 'requests_json_generator',
        'target': 'do_something_with_items',
        'requires_resources': ['dataproc-cluster'],
        'properties': {
            'url': 'http://my.host.com/my-endpoint',
            'list_json_key': 'my-key',
        }
    }

    g = plugins.manager.generators(generator_config)

    assert g.type == NodeTypes.GENERATOR

    assert g.resolve_properties(ExecutionContext(referrer=None, resources={})).values == {
        'url': 'http://my.host.com/my-endpoint',
        'list_json_key': 'my-key',
        'timeout_sec': 5,
        'headers': {}
    }


# Tests for batching functionality

BASE_GENERATOR_CONFIG = {
    'name': 'test_generator',
    'type': 'list_generator',
    'target': 'some_target',
    'properties': {
        'items': ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    }
}

GENERATOR_CONFIG_YAML = """
name: list_generator
iterator_builder_method_code: return items
item_name_builder_code: return item
parameters_jsonschema:
    properties:
        items:
            type: array
            items:
                type: string
    additionalProperties: false
    required:
        - items
"""


class BatchingTestHelper(object):
    """
    Helper class to reduce code required to test code generation under different cases.
    """

    def __init__(self):
        self.builder = PrimaryDagBuilder(None, None, None, None)
        self.generator_spec_schema = GeneratorSpecSchema().load(yaml.load(GENERATOR_CONFIG_YAML))

    def build_generator_node(self, batching_config):
        node_config = copy.deepcopy(BASE_GENERATOR_CONFIG)
        if batching_config is not None:
            node_config['batching'] = batching_config

        return GeneratorNode(config=self.generator_spec_schema.data, item=node_config)

    def run_preamble_template_test(self, batching_config):
        node = self.build_generator_node(batching_config)
        template = self.builder.get_jinja_template('generator_preamble.j2')

        rendered = template.render(
            generator_operator_name='foo',
            referring_node=node
        )

        items_batch_name_regex = re.compile(r'\s+items,\s+batch_name,')
        item_item_name_regex = re.compile(r'\s+item,\s+item_name,')

        items_batch_name_match = items_batch_name_regex.search(rendered)
        item_item_name_match = item_item_name_regex.search(rendered)

        return {
            'items_batch_name': items_batch_name_match,
            'item_item_name': item_item_name_match
        }

    def run_operator_template_test(self, batching_config):
        node = self.build_generator_node(batching_config)
        template = self.builder.get_jinja_template('generator_operator.j2')

        node.resolve_properties(
            execution_context=ExecutionContext(None, {}),
            default_task_args={},
            base_operator_loader=None,
            preprocessor_loader=None
        )

        rendered = template.render(
            node=node,
            upstream_dependencies='upstream_foo',
            downstream_dependencies='downstream_bar'
        )

        item_name_builder_regex = re.compile(r'.*def %s_item_name_builder\(.*' % node.name)
        batch_name_builder_regex = re.compile(r'.*def %s_batch_name_builder\(.*' % node.name)
        filter_helper_regex = re.compile(r'.*def generator_helper_filter_with_blocklist\(.*')
        grouped_helper_regex = re.compile(r'.*def generator_helper_grouped_list\(.*')
        builder_invocation = r'\s+%s_builder\(\s+index = index,' % node.target
        items_batch_name_regex = re.compile(
            r'%s\s+items = items,\s+batch_name = batch_name,' % builder_invocation
        )
        item_item_name_regex = re.compile(
            r'%s\s+item = item,\s+item_name = item_name,' % builder_invocation
        )

        return {
            'item_name_builder': item_name_builder_regex.search(rendered),
            'batch_name_builder': batch_name_builder_regex.search(rendered),
            'filter_helper': filter_helper_regex.search(rendered),
            'grouped_helper': grouped_helper_regex.search(rendered),
            'items_batch_name': items_batch_name_regex.search(rendered),
            'item_item_name': item_item_name_regex.search(rendered),
        }


HELPER = BatchingTestHelper()


def test_batching_enabled_enabled():
    batching_config = {'batch_size': 3}
    node = HELPER.build_generator_node(batching_config)

    assert node.batching_enabled is True


def test_batching_enabled_disabled():
    batching_config = {'batch_size': 3, 'disabled': True}
    node = HELPER.build_generator_node(batching_config)

    assert node.batching_enabled is False


def test_batching_enabled_undefined():
    node = HELPER.build_generator_node(None)

    assert node.batching_enabled is False


def test_preamble_template_batching_enabled():
    batching_config = {'batch_size': 3}
    matches = HELPER.run_preamble_template_test(batching_config)

    assert matches['items_batch_name'] is not None
    assert matches['item_item_name'] is None


def test_preamble_template_batching_disabled():
    batching_config = {'batch_size': 3, 'disabled': True}
    matches = HELPER.run_preamble_template_test(batching_config)

    assert matches['item_item_name'] is not None
    assert matches['items_batch_name'] is None


def test_preamble_template_batching_undefined():
    matches = HELPER.run_preamble_template_test(None)

    assert matches['item_item_name'] is not None
    assert matches['items_batch_name'] is None


def test_operator_template_batching_enabled():
    batching_config = {'batch_size': 3}
    matches = HELPER.run_operator_template_test(batching_config)

    assert matches['item_name_builder'] is not None
    assert matches['batch_name_builder'] is not None
    assert matches['filter_helper'] is not None
    assert matches['grouped_helper'] is not None
    assert matches['items_batch_name'] is not None
    assert matches['item_item_name'] is None


def test_operator_template_batching_disabled():
    batching_config = {'batch_size': 3, 'disabled': True}
    matches = HELPER.run_operator_template_test(batching_config)

    assert matches['item_name_builder'] is not None
    assert matches['batch_name_builder'] is None
    assert matches['filter_helper'] is None
    assert matches['grouped_helper'] is None
    assert matches['items_batch_name'] is None
    assert matches['item_item_name'] is not None


def test_operator_template_batching_undefined():
    matches = HELPER.run_operator_template_test(None)

    assert matches['item_name_builder'] is not None
    assert matches['batch_name_builder'] is None
    assert matches['filter_helper'] is None
    assert matches['grouped_helper'] is None
    assert matches['items_batch_name'] is None
    assert matches['item_item_name'] is not None
