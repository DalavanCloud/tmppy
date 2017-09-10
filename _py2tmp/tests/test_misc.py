#  Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from py2tmp.testing import *

@assert_conversion_fails
def test_global_variable_error():
    x = 1  # error: This Python construct is not supported in TMPPy

@assert_conversion_fails
def test_reference_to_undefined_identifier_error():
    '''
    def f(x: bool):
        return undefined_identifier  # error: Reference to undefined variable/function: undefined_identifier
    '''