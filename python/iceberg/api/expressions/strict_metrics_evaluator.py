# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from .expressions import Expressions, ExpressionVisitors
from ..expressions.binder import Binder
from ..types import Conversions


class StrictMetricsEvaluator(object):

    def __init__(self, schema, unbound):
        self.schema = schema
        self.struct = schema.as_struct()
        self.expr = Binder.bind(self.struct, Expressions.rewrite_not(unbound))
        self._visitors = None

    def _visitor(self):
        if self._visitors is None:
            self._visitors = StrictMetricsEvaluator.MetricsEvalVisitor(self.expr, self.schema, self.struct)

        return self._visitors

    def eval(self, file):
        return self._visitor().eval(file)

    class MetricsEvalVisitor(ExpressionVisitors.BoundExpressionVisitor):
        ROWS_MUST_MATCH = True
        ROWS_MIGHT_NOT_MATCH = False

        def __init__(self, expr, schema, struct):
            self.expr = expr
            self.schema = schema
            self.struct = struct
            self.value_counts = None
            self.null_counts = None
            self.lower_bounds = None
            self.upper_bounds = None

        def eval(self, file):
            if file.record_count() <= 0:
                return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            self.value_counts = file.value_counts()
            self.null_counts = file.null_value_counts()
            self.lower_bounds = file.lower_bounds()
            self.upper_bounds = file.upper_bounds()

            return ExpressionVisitors.visit(self.expr, self)

        def always_true(self):
            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

        def always_false(self):
            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def not_(self, result):
            return not result

        def and_(self, left_result, right_result):
            return left_result and right_result

        def or_(self, left_result, right_result):
            return left_result or right_result

        def is_null(self, ref):
            id = ref.field_id
            if self.struct.field(id=id) is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.value_counts is not None and self.value_counts.get(id) is not None \
                    and self.null_counts is not None and self.null_counts.get(id) is not None \
                    and self.value_counts.get(id) - self.null_counts.get(id) == 0:
                return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def not_null(self, ref):
            id = ref.field_id
            if self.struct.field(id=id) is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.null_counts is not None and self.null_counts.get(id, -1) == 0:
                return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def lt(self, ref, lit):
            # Rows must match when: <----------Min----Max---X------->
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.upper_bounds is not None and id in self.upper_bounds:
                upper = Conversions.from_byte_buffer(field.type, self.upper_bounds.get(id))
                if upper < lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def lt_eq(self, ref, lit):
            # Rows must match when: <----------Min----Max---X------->
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.upper_bounds is not None and id in self.upper_bounds:
                upper = Conversions.from_byte_buffer(field.type, self.upper_bounds.get(id))
                if upper <= lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def gt(self, ref, lit):
            # Rows must match when: <-------X---Min----Max---------->
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.lower_bounds is not None and id in self.lower_bounds:
                lower = Conversions.from_byte_buffer(field.type, self.lower_bounds.get(id))
                if lower > lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def gt_eq(self, ref, lit):
            # Rows must match when: <-------X---Min----Max---------->
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.lower_bounds is not None and id in self.lower_bounds:
                lower = Conversions.from_byte_buffer(field.type, self.lower_bounds.get(id))
                if lower >= lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def eq(self, ref, lit):
            # Rows must match when Min == X == Max
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.lower_bounds is not None and id in self.lower_bounds \
                    and self.upper_bounds is not None and id in self.upper_bounds:
                lower = Conversions.from_byte_buffer(field.type, self.lower_bounds.get(id))
                if lower != lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

                upper = Conversions.from_byte_buffer(field.type, self.upper_bounds.get(id))

                if upper != lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

                return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def not_eq(self, ref, lit):
            # Rows must match when X < Min or Max < X because it is not in the range
            id = ref.field_id

            field = self.struct.field(id=id)

            if field is None:
                raise RuntimeError("Cannot filter by nested column: %s" % self.schema.find_field(id))

            if self.lower_bounds is not None and id in self.lower_bounds:
                lower = Conversions.from_byte_buffer(field.type, self.lower_bounds.get(id))
                if lower > lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            if self.upper_bounds is not None and id in self.upper_bounds:
                upper = Conversions.from_byte_buffer(field.type, self.upper_bounds.get(id))

                if upper < lit.value:
                    return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MUST_MATCH

            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def in_(self, ref, lit):
            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH

        def not_in(self, ref, lit):
            return StrictMetricsEvaluator.MetricsEvalVisitor.ROWS_MIGHT_NOT_MATCH
