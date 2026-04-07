# localization_study_95_structural_reused_ready

Instances evaluated: 93

## Aggregate Metrics

- Retrieved Top-1 file match: 15/93 (16.1%, 95% CI 10.0-24.9%)
- Retrieved Top-3 file match: 25/93 (26.9%, 95% CI 18.9-36.7%)
- Retrieved Top-5 file match: 26/93 (28.0%, 95% CI 19.9-37.8%)
- Summary mentions gold file: 33/93 (35.5%, 95% CI 26.5-45.6%)
- Final target in gold file: 26/93 (28.0%, 95% CI 19.9-37.8%)
- Final target within gold hunk: 8/93 (8.6%, 95% CI 4.4-16.1%)
- Semantic correct file: 40/93 (43.0%, 95% CI 33.4-53.2%)
- Semantic correct function: 33/93 (35.5%, 95% CI 26.5-45.6%)
- Semantic correct fix mechanism: 70/93 (75.3%, 95% CI 65.6-82.9%)
- Semantic localization match: 30/93 (32.3%, 95% CI 23.6-42.3%)
- Weak proxy Graph found issue: 39/93 (41.9%, 95% CI 32.4-52.1%)

## Repo Breakdown

| Repo | Sample | Semantic Localization | Weak Graph Found Issue | Audited Graph Found Issue |
|---|---:|---|---|---|
| astropy/astropy | 4 | 3/4 (75.0%, 95% CI 30.1-95.4%) | 3/4 (75.0%, 95% CI 30.1-95.4%) | N/A |
| django/django | 38 | 11/38 (28.9%, 95% CI 17.0-44.8%) | 14/38 (36.8%, 95% CI 23.4-52.7%) | N/A |
| matplotlib/matplotlib | 8 | 4/8 (50.0%, 95% CI 21.5-78.5%) | 4/8 (50.0%, 95% CI 21.5-78.5%) | N/A |
| mwaskom/seaborn | 3 | 1/3 (33.3%, 95% CI 6.1-79.2%) | 2/3 (66.7%, 95% CI 20.8-93.9%) | N/A |
| pallets/flask | 3 | 2/3 (66.7%, 95% CI 20.8-93.9%) | 2/3 (66.7%, 95% CI 20.8-93.9%) | N/A |
| psf/requests | 4 | 0/4 (0.0%, 95% CI 0.0-49.0%) | 2/4 (50.0%, 95% CI 15.0-85.0%) | N/A |
| pydata/xarray | 4 | 0/4 (0.0%, 95% CI 0.0-49.0%) | 0/4 (0.0%, 95% CI 0.0-49.0%) | N/A |
| pylint-dev/pylint | 4 | 0/4 (0.0%, 95% CI 0.0-49.0%) | 0/4 (0.0%, 95% CI 0.0-49.0%) | N/A |
| pytest-dev/pytest | 8 | 1/8 (12.5%, 95% CI 2.2-47.1%) | 2/8 (25.0%, 95% CI 7.1-59.1%) | N/A |
| scikit-learn/scikit-learn | 9 | 6/9 (66.7%, 95% CI 35.4-87.9%) | 7/9 (77.8%, 95% CI 45.3-93.7%) | N/A |
| sphinx-doc/sphinx | 7 | 2/7 (28.6%, 95% CI 8.2-64.1%) | 3/7 (42.9%, 95% CI 15.8-75.0%) | N/A |
| sympy/sympy | 1 | 0/1 (0.0%, 95% CI 0.0-79.3%) | 0/1 (0.0%, 95% CI 0.0-79.3%) | N/A |

## Failure Taxonomy

| Bucket | Count |
|---|---:|
| retrieval missed correct file | 51 |
| localized successfully | 39 |
| summary understood issue but named wrong implementation site | 2 |
| selector drifted away from summary | 1 |
## Per-Instance Results

| Instance | Repo | Gold Files | Retrieved Top Files | Target | Top-3 | Summary | Target File | Target Hunk | Semantic | Weak Graph | Taxonomy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| astropy__astropy-12907 | astropy/astropy | astropy/modeling/separable.py | astropy/modeling/separable.py, astropy/stats/info_theory.py, astropy/__init__.py | astropy/modeling/separable.py:245 | yes | yes | yes | yes | yes | yes | localized successfully |
| astropy__astropy-14995 | astropy/astropy | astropy/nddata/mixins/ndarithmetic.py | astropy/nddata/bitmask.py, astropy/utils/masked/function_helpers.py, astropy/io/votable/table.py | astropy/utils/masked/function_helpers.py:173 | no | no | no | no | no | no | retrieval missed correct file |
| astropy__astropy-6938 | astropy/astropy | astropy/io/fits/fitsrec.py | astropy/io/fits/card.py, astropy/io/fits/scripts/fitsdiff.py, astropy/modeling/blackbody.py | astropy/io/fits/fitsrec.py:1292 | no | no | yes | no | yes | yes | localized successfully |
| astropy__astropy-7746 | astropy/astropy | astropy/wcs/wcs.py | astropy/nddata/ccddata.py, astropy/wcs/wcs.py, astropy/nddata/decorators.py | astropy/wcs/wcs.py:3067 | yes | no | yes | no | yes | yes | localized successfully |
| django__django-11019 | django/django | django/forms/widgets.py | django/db/models/query_utils.py, django/forms/models.py, django/contrib/admin/utils.py | django/forms/models.py:31 | no | yes | no | no | no | no | retrieval missed correct file |
| django__django-11049 | django/django | django/db/models/fields/__init__.py | django/utils/duration.py, django/contrib/postgres/utils.py, django/utils/ipv6.py | django/utils/duration.py:20 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-11099 | django/django | django/contrib/auth/validators.py | django/middleware/csrf.py, django/utils/text.py, django/template/defaulttags.py | django/contrib/auth/validators.py:10 | no | yes | yes | yes | yes | yes | localized successfully |
| django__django-11133 | django/django | django/http/response.py | django/db/backends/sqlite3/base.py, django/contrib/gis/utils/srs.py, django/core/checks/database.py | django/utils/encoding.py:82 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-11422 | django/django | django/utils/autoreload.py | django/utils/autoreload.py, django/template/defaultfilters.py, django/views/static.py | django/utils/autoreload.py:580 | yes | yes | yes | no | yes | yes | localized successfully |
| django__django-11564 | django/django | django/conf/__init__.py | django/contrib/staticfiles/utils.py, django/template/context_processors.py, django/templatetags/static.py | django/templatetags/static.py:146 | no | no | no | no | yes | yes | localized successfully |
| django__django-11815 | django/django | django/db/migrations/serializer.py | django/db/migrations/migration.py, django/utils/translation/trans_real.py, django/db/migrations/serializer.py | django/db/migrations/serializer.py:339 | yes | yes | yes | no | yes | yes | localized successfully |
| django__django-11905 | django/django | django/db/models/lookups.py | django/contrib/admin/utils.py, django/test/utils.py, django/views/decorators/http.py | django/contrib/admin/utils.py:61 | no | no | no | no | yes | yes | localized successfully |
| django__django-11910 | django/django | django/db/migrations/autodetector.py | django/db/migrations/operations/utils.py, django/core/checks/model_checks.py, django/db/models/query_utils.py | django/db/models/query_utils.py:282 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-11964 | django/django | django/db/models/enums.py | django/contrib/contenttypes/fields.py, django/db/models/fields/related_descriptors.py, django/contrib/gis/utils/ogrinspect.py | django/forms/models.py:31 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-12308 | django/django | django/contrib/admin/utils.py | django/template/defaulttags.py, django/contrib/gis/views.py, django/utils/timesince.py | django/template/defaultfilters.py:88 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-12497 | django/django | django/db/models/fields/related.py | django/db/models/fields/related.py, django/core/serializers/base.py, django/db/models/fields/related_descriptors.py | django/db/models/fields/related.py:1285 | yes | yes | yes | no | yes | yes | localized successfully |
| django__django-12700 | django/django | django/views/debug.py | django/contrib/auth/hashers.py, django/utils/cache.py, django/forms/models.py | django/conf/global_settings.py:309 | no | no | no | no | no | yes | localized successfully |
| django__django-12983 | django/django | django/utils/text.py | django/contrib/gis/views.py, django/utils/text.py, django/http/cookie.py | django/utils/text.py:405 | yes | yes | yes | yes | yes | yes | localized successfully |
| django__django-13220 | django/django | django/core/exceptions.py | django/template/defaulttags.py, django/utils/http.py, django/core/files/move.py | django/core/checks/messages.py:19 | no | yes | no | no | no | no | retrieval missed correct file |
| django__django-13265 | django/django | django/db/migrations/autodetector.py | django/core/checks/model_checks.py, django/db/models/fields/related.py, django/contrib/admin/utils.py | django/db/models/fields/related.py:13 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-13447 | django/django | django/contrib/admin/sites.py | django/urls/base.py, django/contrib/admin/utils.py, django/core/serializers/__init__.py | django/contrib/admin/utils.py:156 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-13590 | django/django | django/db/models/sql/query.py | django/template/defaulttags.py, django/contrib/admindocs/utils.py, django/utils/regex_helper.py | django/db/models/utils.py:25 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-13658 | django/django | django/core/management/__init__.py | django/core/management/__init__.py, django/core/management/utils.py, django/core/management/base.py | django/core/management/__init__.py:411 | yes | no | yes | no | yes | yes | localized successfully |
| django__django-13933 | django/django | django/forms/models.py | django/views/static.py, django/core/validators.py, django/utils/ipv6.py | django/forms/fields.py:807 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-14016 | django/django | django/db/models/query_utils.py | django/contrib/contenttypes/fields.py, django/contrib/admin/utils.py, django/template/smartif.py | django/utils/log.py:165 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-14017 | django/django | django/db/models/query_utils.py | django/template/defaultfilters.py, django/db/models/options.py, django/utils/hashable.py | django/db/models/query_utils.py:65 | no | yes | yes | no | no | yes | localized successfully |
| django__django-14238 | django/django | django/db/models/fields/__init__.py | django/contrib/admin/decorators.py, django/db/models/base.py, django/db/models/query_utils.py | django/db/models/base.py:1311 | no | no | no | no | no | yes | localized successfully |
| django__django-14411 | django/django | django/contrib/auth/forms.py | django/contrib/admin/utils.py, django/contrib/admin/actions.py, django/contrib/gis/sitemaps/views.py | django/contrib/admin/utils.py:312 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-14534 | django/django | django/forms/boundfield.py | django/forms/models.py, django/contrib/contenttypes/views.py, django/core/checks/model_checks.py | django/core/management/commands/loaddata.py:288 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-14580 | django/django | django/db/migrations/serializer.py | django/contrib/admin/decorators.py, django/contrib/gis/utils/ogrinspect.py, django/views/defaults.py | django/forms/models.py:35 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-14672 | django/django | django/db/models/fields/reverse_related.py | django/contrib/auth/__init__.py, django/core/checks/model_checks.py, django/core/management/__init__.py | django/conf/__init__.py:150 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-15061 | django/django | django/forms/widgets.py | django/db/models/query.py, django/db/models/fields/related_descriptors.py, django/forms/models.py | django/contrib/admin/templatetags/admin_modify.py:48 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-15388 | django/django | django/template/autoreload.py | django/core/servers/basehttp.py, django/utils/autoreload.py, django/core/handlers/wsgi.py | django/core/servers/basehttp.py:34 | no | yes | no | no | no | no | retrieval missed correct file |
| django__django-15781 | django/django | django/core/management/base.py | django/core/management/utils.py, django/contrib/auth/password_validation.py, django/contrib/admin/utils.py | django/core/management/__init__.py:57 | no | yes | no | no | no | no | retrieval missed correct file |
| django__django-16041 | django/django | django/forms/formsets.py | django/template/smartif.py, django/test/client.py, django/template/loader_tags.py | django/forms/models.py:1033 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-16229 | django/django | django/forms/boundfield.py | django/forms/models.py, django/contrib/contenttypes/forms.py, django/contrib/admin/utils.py | django/forms/models.py:72 | no | no | no | no | yes | yes | localized successfully |
| django__django-16408 | django/django | django/db/models/sql/compiler.py | django/db/models/query.py, django/db/models/fields/related_descriptors.py, django/core/checks/model_checks.py | django/db/models/query.py:2616 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-16527 | django/django | django/contrib/admin/templatetags/admin_modify.py | django/contrib/admin/templatetags/admin_modify.py, django/contrib/admin/templatetags/log.py, django/contrib/admin/decorators.py | django/contrib/admin/templatetags/admin_modify.py:99 | yes | yes | yes | no | yes | yes | localized successfully |
| django__django-16595 | django/django | django/db/migrations/operations/fields.py | django/contrib/contenttypes/management/__init__.py, django/db/migrations/serializer.py, django/db/migrations/utils.py | django/db/migrations/migration.py:14 | no | no | no | no | no | no | retrieval missed correct file |
| django__django-16873 | django/django | django/template/defaultfilters.py | django/template/defaulttags.py, django/template/defaultfilters.py | django/template/defaultfilters.py:585 | yes | yes | yes | no | yes | yes | localized successfully |
| django__django-16910 | django/django | django/db/models/sql/query.py | django/contrib/admin/utils.py, django/db/models/query_utils.py, django/contrib/auth/migrations/0011_update_proxy_permissions.py | django/db/models/query_utils.py:318 | no | yes | no | no | no | no | retrieval missed correct file |
| django__django-17051 | django/django | django/db/models/query.py | django/db/models/fields/related_descriptors.py, django/db/models/query_utils.py, django/db/transaction.py | django/db/models/fields/related_descriptors.py:102 | no | yes | no | no | no | no | retrieval missed correct file |
| matplotlib__matplotlib-22711 | matplotlib/matplotlib | lib/matplotlib/widgets.py | examples/widgets/range_slider.py, lib/matplotlib/backends/backend_pgf.py, lib/matplotlib/__init__.py | lib/matplotlib/widgets.py:912 | no | yes | yes | yes | yes | yes | localized successfully |
| matplotlib__matplotlib-23299 | matplotlib/matplotlib | lib/matplotlib/__init__.py | lib/matplotlib/pyplot.py, lib/matplotlib/testing/decorators.py, lib/matplotlib/sphinxext/plot_directive.py | lib/matplotlib/pyplot.py:770 | no | no | no | no | yes | yes | localized successfully |
| matplotlib__matplotlib-23314 | matplotlib/matplotlib | lib/mpl_toolkits/mplot3d/axes3d.py | lib/matplotlib/pyplot.py, examples/axisartist/simple_axis_pad.py, examples/axisartist/axis_direction.py | lib/matplotlib/pyplot.py:1402 | no | no | no | no | no | no | retrieval missed correct file |
| matplotlib__matplotlib-23913 | matplotlib/matplotlib | lib/matplotlib/legend.py | lib/matplotlib/backends/qt_editor/figureoptions.py, lib/matplotlib/legend.py, tools/run_examples.py | lib/matplotlib/legend.py:308 | yes | yes | yes | no | yes | yes | localized successfully |
| matplotlib__matplotlib-24265 | matplotlib/matplotlib | lib/matplotlib/style/core.py | lib/matplotlib/__init__.py, lib/matplotlib/sphinxext/plot_directive.py, examples/text_labels_and_annotations/font_table.py | lib/matplotlib/__init__.py:694 | no | yes | no | no | no | no | retrieval missed correct file |
| matplotlib__matplotlib-24334 | matplotlib/matplotlib | lib/matplotlib/axis.py | lib/matplotlib/rcsetup.py, lib/matplotlib/pyplot.py, lib/matplotlib/patheffects.py | lib/matplotlib/pyplot.py:1041 | no | no | no | no | no | no | retrieval missed correct file |
| matplotlib__matplotlib-25442 | matplotlib/matplotlib | lib/matplotlib/offsetbox.py | lib/matplotlib/_api/__init__.py, lib/matplotlib/backends/backend_qt5.py, lib/matplotlib/cm.py | lib/matplotlib/offsetbox.py:1228 | no | yes | yes | no | no | no | retrieval missed correct file |
| matplotlib__matplotlib-25498 | matplotlib/matplotlib | lib/matplotlib/colorbar.py | lib/matplotlib/colors.py, setup.py, galleries/examples/images_contours_and_fields/multi_image.py | lib/matplotlib/colorbar.py:511 | no | yes | yes | no | yes | yes | localized successfully |
| mwaskom__seaborn-2848 | mwaskom/seaborn | seaborn/_oldcore.py | seaborn/axisgrid.py, seaborn/categorical.py, seaborn/distributions.py | seaborn/axisgrid.py:2039 | no | no | no | no | no | yes | localized successfully |
| mwaskom__seaborn-3010 | mwaskom/seaborn | seaborn/_stats/regression.py | seaborn/utils.py, seaborn/regression.py, seaborn/matrix.py | seaborn/_core/plot.py:1073 | no | no | no | no | no | no | retrieval missed correct file |
| mwaskom__seaborn-3407 | mwaskom/seaborn | seaborn/axisgrid.py | seaborn/axisgrid.py, seaborn/matrix.py, doc/sphinxext/tutorial_builder.py | seaborn/axisgrid.py:2114 | yes | yes | yes | no | yes | yes | localized successfully |
| pallets__flask-4045 | pallets/flask | src/flask/blueprints.py | src/flask/helpers.py, src/flask/debughelpers.py, examples/tutorial/flaskr/__init__.py | src/flask/helpers.py:212 | no | no | no | no | no | no | retrieval missed correct file |
| pallets__flask-4992 | pallets/flask | src/flask/config.py | src/flask/helpers.py, src/flask/json/__init__.py, src/flask/cli.py | src/flask/config.py:248 | no | yes | yes | yes | yes | yes | localized successfully |
| pallets__flask-5063 | pallets/flask | src/flask/cli.py | src/flask/cli.py, src/flask/helpers.py | src/flask/cli.py:1004 | yes | yes | yes | yes | yes | yes | localized successfully |
| psf__requests-1963 | psf/requests | requests/sessions.py | requests/api.py, requests/cookies.py, requests/utils.py | requests/api.py:22 | no | no | no | no | no | yes | localized successfully |
| psf__requests-2674 | psf/requests | requests/adapters.py | requests/packages/urllib3/__init__.py, requests/packages/urllib3/contrib/pyopenssl.py, requests/packages/urllib3/util/ssl_.py | requests/packages/urllib3/__init__.py:65 | no | no | no | no | no | no | retrieval missed correct file |
| psf__requests-3362 | psf/requests | requests/utils.py | requests/utils.py, requests/packages/urllib3/fields.py, requests/api.py | requests/api.py:49 | yes | yes | no | no | no | yes | localized successfully |
| psf__requests-863 | psf/requests | requests/models.py | requests/api.py, requests/hooks.py, requests/packages/oauthlib/oauth2/draft25/tokens.py | requests/api.py:51 | no | no | no | no | no | no | retrieval missed correct file |
| pydata__xarray-3364 | pydata/xarray | xarray/core/concat.py | xarray/core/combine.py, xarray/core/concat.py, xarray/backends/api.py | xarray/core/combine.py:166 | yes | no | no | no | no | no | summary understood issue but named wrong implementation site |
| pydata__xarray-4094 | pydata/xarray | xarray/core/dataarray.py | xarray/core/formatting.py, xarray/core/merge.py, xarray/core/variable.py | xarray/core/merge.py:142 | no | no | no | no | no | no | retrieval missed correct file |
| pydata__xarray-4248 | pydata/xarray | xarray/core/formatting.py | xarray/coding/variables.py, xarray/core/merge.py, xarray/coding/times.py | xarray/core/merge.py:829 | no | no | no | no | no | no | retrieval missed correct file |
| pydata__xarray-5131 | pydata/xarray | xarray/core/groupby.py | xarray/coding/cftimeindex.py, xarray/core/computation.py, xarray/core/resample_cftime.py | xarray/core/formatting.py:516 | no | no | no | no | no | no | retrieval missed correct file |
| pylint-dev__pylint-7080 | pylint-dev/pylint | pylint/lint/expand_modules.py | pylint/config/_pylint_config/generate_command.py, pylint/checkers/imports.py, doc/exts/pylint_options.py | pylint/config/_pylint_config/setup.py:38 | no | no | no | no | no | no | retrieval missed correct file |
| pylint-dev__pylint-7114 | pylint-dev/pylint | pylint/lint/expand_modules.py | pylint/checkers/utils.py, pylint/checkers/classes/class_checker.py, pylint/lint/base_options.py | pylint/checkers/typecheck.py:533 | no | no | no | no | no | no | retrieval missed correct file |
| pylint-dev__pylint-7228 | pylint-dev/pylint | pylint/config/argument.py | pylint/config/config_initialization.py, pylint/testutils/_run.py, pylint/config/find_default_config_files.py | pylint/__init__.py:32 | no | no | no | no | no | no | retrieval missed correct file |
| pylint-dev__pylint-7993 | pylint-dev/pylint | pylint/reporters/text.py | pylint/lint/utils.py, doc/exts/pylint_messages.py, pylint/config/_pylint_config/help_message.py | pylint/config/_pylint_config/help_message.py:42 | no | yes | no | no | no | no | retrieval missed correct file |
| pytest-dev__pytest-11143 | pytest-dev/pytest | src/_pytest/assertion/rewrite.py | src/_pytest/assertion/rewrite.py, src/_pytest/assertion/__init__.py | src/_pytest/pathlib.py:533 | yes | yes | no | no | no | no | selector drifted away from summary |
| pytest-dev__pytest-5495 | pytest-dev/pytest | src/_pytest/assertion/util.py | src/_pytest/assertion/rewrite.py, src/_pytest/assertion/__init__.py, src/_pytest/assertion/util.py | src/_pytest/assertion/rewrite.py:327 | yes | yes | no | no | no | yes | localized successfully |
| pytest-dev__pytest-6116 | pytest-dev/pytest | src/_pytest/main.py | src/_pytest/logging.py, src/_pytest/python.py, src/_pytest/python_api.py | src/_pytest/python.py:62 | no | no | no | no | no | no | retrieval missed correct file |
| pytest-dev__pytest-7168 | pytest-dev/pytest | src/_pytest/_io/saferepr.py | src/_pytest/main.py, src/_pytest/_io/saferepr.py, src/_pytest/python.py | src/_pytest/runner.py:147 | yes | no | no | no | no | no | summary understood issue but named wrong implementation site |
| pytest-dev__pytest-7220 | pytest-dev/pytest | src/_pytest/nodes.py | src/_pytest/main.py, src/_pytest/tmpdir.py, src/_pytest/assertion/rewrite.py | src/_pytest/main.py:77 | no | no | no | no | no | no | retrieval missed correct file |
| pytest-dev__pytest-7373 | pytest-dev/pytest | src/_pytest/mark/evaluate.py | src/_pytest/skipping.py, src/_pytest/assertion/__init__.py, src/_pytest/outcomes.py | src/_pytest/skipping.py:87 | no | yes | no | no | no | no | retrieval missed correct file |
| pytest-dev__pytest-8906 | pytest-dev/pytest | src/_pytest/python.py | src/_pytest/logging.py, scripts/release.py, src/_pytest/mark/structures.py | src/_pytest/outcomes.py:172 | no | no | no | no | no | no | retrieval missed correct file |
| pytest-dev__pytest-9359 | pytest-dev/pytest | src/_pytest/_code/source.py | testing/python/approx.py, src/_pytest/assertion/rewrite.py, extra/get_issues.py | src/_pytest/assertion/rewrite.py:453 | no | no | no | no | yes | yes | localized successfully |
| scikit-learn__scikit-learn-10297 | scikit-learn/scikit-learn | sklearn/linear_model/ridge.py | sklearn/utils/validation.py, sklearn/pipeline.py, sklearn/linear_model/setup.py | doc/tutorial/machine_learning_map/pyparsing.py:2826 | no | no | no | no | no | no | retrieval missed correct file |
| scikit-learn__scikit-learn-10508 | scikit-learn/scikit-learn | sklearn/preprocessing/label.py | sklearn/utils/estimator_checks.py, sklearn/model_selection/_validation.py, sklearn/linear_model/base.py | sklearn/preprocessing/label.py:135 | no | yes | yes | no | yes | yes | localized successfully |
| scikit-learn__scikit-learn-10949 | scikit-learn/scikit-learn | sklearn/utils/validation.py | sklearn/utils/estimator_checks.py, sklearn/utils/__init__.py, sklearn/utils/validation.py | sklearn/utils/validation.py:577 | yes | no | yes | no | yes | yes | localized successfully |
| scikit-learn__scikit-learn-11281 | scikit-learn/scikit-learn | sklearn/mixture/base.py | sklearn/mixture/gaussian_mixture.py, sklearn/mixture/base.py, sklearn/mixture/gmm.py | sklearn/mixture/gaussian_mixture.py:667 | yes | yes | no | no | no | yes | localized successfully |
| scikit-learn__scikit-learn-13779 | scikit-learn/scikit-learn | sklearn/ensemble/voting.py | sklearn/ensemble/voting.py, sklearn/utils/estimator_checks.py, sklearn/metrics/scorer.py | sklearn/ensemble/voting.py:32 | yes | no | yes | no | yes | yes | localized successfully |
| scikit-learn__scikit-learn-14092 | scikit-learn/scikit-learn | sklearn/neighbors/nca.py | examples/neighbors/plot_nca_illustration.py, sklearn/utils/estimator_checks.py, sklearn/linear_model/huber.py | sklearn/utils/validation.py:636 | no | no | no | no | no | no | retrieval missed correct file |
| scikit-learn__scikit-learn-14894 | scikit-learn/scikit-learn | sklearn/svm/base.py | sklearn/svm/base.py, sklearn/datasets/base.py, sklearn/svm/setup.py | sklearn/svm/base.py:289 | yes | yes | yes | yes | yes | yes | localized successfully |
| scikit-learn__scikit-learn-15512 | scikit-learn/scikit-learn | sklearn/cluster/_affinity_propagation.py | sklearn/cluster/_affinity_propagation.py, sklearn/cluster/_spectral.py, sklearn/cluster/_hierarchical.py | sklearn/cluster/_affinity_propagation.py:33 | yes | no | yes | no | yes | yes | localized successfully |
| scikit-learn__scikit-learn-25500 | scikit-learn/scikit-learn | sklearn/isotonic.py | sklearn/isotonic.py, benchmarks/bench_isotonic.py, sklearn/calibration.py | sklearn/calibration.py:529 | yes | no | no | no | yes | yes | localized successfully |
| sphinx-doc__sphinx-11445 | sphinx-doc/sphinx | sphinx/util/rst.py | sphinx/directives/__init__.py, sphinx/domains/changeset.py, sphinx/directives/other.py | sphinx/directives/__init__.py:165 | no | no | no | no | no | no | retrieval missed correct file |
| sphinx-doc__sphinx-7686 | sphinx-doc/sphinx | sphinx/ext/autosummary/generate.py | sphinx/ext/autosummary/generate.py, sphinx/ext/autosummary/__init__.py, sphinx/ext/autodoc/__init__.py | sphinx/ext/autosummary/generate.py:207 | yes | yes | yes | yes | no | yes | localized successfully |
| sphinx-doc__sphinx-7738 | sphinx-doc/sphinx | sphinx/ext/napoleon/docstring.py | sphinx/ext/napoleon/__init__.py, sphinx/ext/autodoc/importer.py, sphinx/util/inspect.py | sphinx/ext/napoleon/__init__.py:370 | no | no | no | no | no | no | retrieval missed correct file |
| sphinx-doc__sphinx-7975 | sphinx-doc/sphinx | sphinx/environment/adapters/indexentries.py | sphinx/util/nodes.py, sphinx/roles.py, sphinx/domains/std.py | sphinx/roles.py:559 | no | no | no | no | no | no | retrieval missed correct file |
| sphinx-doc__sphinx-8474 | sphinx-doc/sphinx | sphinx/domains/std.py | sphinx/ext/duration.py, sphinx/ext/intersphinx.py, sphinx/ext/viewcode.py | sphinx/ext/duration.py:63 | no | no | no | no | no | no | retrieval missed correct file |
| sphinx-doc__sphinx-8506 | sphinx-doc/sphinx | sphinx/domains/std.py | sphinx/domains/rst.py, sphinx/util/docutils.py, sphinx/util/requests.py | sphinx/domains/rst.py:275 | no | no | no | no | yes | yes | localized successfully |
| sphinx-doc__sphinx-8721 | sphinx-doc/sphinx | sphinx/ext/viewcode.py | sphinx/ext/viewcode.py, sphinx/builders/epub3.py, sphinx/builders/_epub_base.py | sphinx/ext/viewcode.py:59 | yes | yes | yes | no | yes | yes | localized successfully |
| sympy__sympy-12481 | sympy/sympy | sympy/combinatorics/permutations.py | sympy/combinatorics/util.py, sympy/utilities/iterables.py, sympy/core/compatibility.py | sympy/combinatorics/util.py:84 | no | no | no | no | no | no | retrieval missed correct file |
