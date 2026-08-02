# Generated from Kql.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .KqlParser import KqlParser
else:
    from KqlParser import KqlParser

# This class defines a complete generic visitor for a parse tree produced by KqlParser.

class KqlVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by KqlParser#top.
    def visitTop(self, ctx:KqlParser.TopContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#query.
    def visitQuery(self, ctx:KqlParser.QueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#statement.
    def visitStatement(self, ctx:KqlParser.StatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#aliasDatabaseStatement.
    def visitAliasDatabaseStatement(self, ctx:KqlParser.AliasDatabaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letStatement.
    def visitLetStatement(self, ctx:KqlParser.LetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letVariableDeclaration.
    def visitLetVariableDeclaration(self, ctx:KqlParser.LetVariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letFunctionDeclaration.
    def visitLetFunctionDeclaration(self, ctx:KqlParser.LetFunctionDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letViewDeclaration.
    def visitLetViewDeclaration(self, ctx:KqlParser.LetViewDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letViewParameterList.
    def visitLetViewParameterList(self, ctx:KqlParser.LetViewParameterListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letMaterializeDeclaration.
    def visitLetMaterializeDeclaration(self, ctx:KqlParser.LetMaterializeDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letEntityGroupDeclaration.
    def visitLetEntityGroupDeclaration(self, ctx:KqlParser.LetEntityGroupDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letFunctionParameterList.
    def visitLetFunctionParameterList(self, ctx:KqlParser.LetFunctionParameterListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scalarParameter.
    def visitScalarParameter(self, ctx:KqlParser.ScalarParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scalarParameterDefault.
    def visitScalarParameterDefault(self, ctx:KqlParser.ScalarParameterDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#tabularParameter.
    def visitTabularParameter(self, ctx:KqlParser.TabularParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#tabularParameterOpenSchema.
    def visitTabularParameterOpenSchema(self, ctx:KqlParser.TabularParameterOpenSchemaContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#tabularParameterRowSchema.
    def visitTabularParameterRowSchema(self, ctx:KqlParser.TabularParameterRowSchemaContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#tabularParameterRowSchemaColumnDeclaration.
    def visitTabularParameterRowSchemaColumnDeclaration(self, ctx:KqlParser.TabularParameterRowSchemaColumnDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letFunctionBody.
    def visitLetFunctionBody(self, ctx:KqlParser.LetFunctionBodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#letFunctionBodyStatement.
    def visitLetFunctionBodyStatement(self, ctx:KqlParser.LetFunctionBodyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternStatement.
    def visitDeclarePatternStatement(self, ctx:KqlParser.DeclarePatternStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternDefinition.
    def visitDeclarePatternDefinition(self, ctx:KqlParser.DeclarePatternDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternParameterList.
    def visitDeclarePatternParameterList(self, ctx:KqlParser.DeclarePatternParameterListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternParameter.
    def visitDeclarePatternParameter(self, ctx:KqlParser.DeclarePatternParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternPathParameter.
    def visitDeclarePatternPathParameter(self, ctx:KqlParser.DeclarePatternPathParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternRule.
    def visitDeclarePatternRule(self, ctx:KqlParser.DeclarePatternRuleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternRuleArgumentList.
    def visitDeclarePatternRuleArgumentList(self, ctx:KqlParser.DeclarePatternRuleArgumentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternRulePathArgument.
    def visitDeclarePatternRulePathArgument(self, ctx:KqlParser.DeclarePatternRulePathArgumentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternRuleArgument.
    def visitDeclarePatternRuleArgument(self, ctx:KqlParser.DeclarePatternRuleArgumentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declarePatternBody.
    def visitDeclarePatternBody(self, ctx:KqlParser.DeclarePatternBodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#restrictAccessStatement.
    def visitRestrictAccessStatement(self, ctx:KqlParser.RestrictAccessStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#restrictAccessStatementEntity.
    def visitRestrictAccessStatementEntity(self, ctx:KqlParser.RestrictAccessStatementEntityContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#setStatement.
    def visitSetStatement(self, ctx:KqlParser.SetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#setStatementOptionValue.
    def visitSetStatementOptionValue(self, ctx:KqlParser.SetStatementOptionValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declareQueryParametersStatement.
    def visitDeclareQueryParametersStatement(self, ctx:KqlParser.DeclareQueryParametersStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#declareQueryParametersStatementParameter.
    def visitDeclareQueryParametersStatementParameter(self, ctx:KqlParser.DeclareQueryParametersStatementParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#queryStatement.
    def visitQueryStatement(self, ctx:KqlParser.QueryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#expression.
    def visitExpression(self, ctx:KqlParser.ExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#pipeExpression.
    def visitPipeExpression(self, ctx:KqlParser.PipeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#pipedOperator.
    def visitPipedOperator(self, ctx:KqlParser.PipedOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#pipeSubExpression.
    def visitPipeSubExpression(self, ctx:KqlParser.PipeSubExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#beforePipeExpression.
    def visitBeforePipeExpression(self, ctx:KqlParser.BeforePipeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#afterPipeOperator.
    def visitAfterPipeOperator(self, ctx:KqlParser.AfterPipeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#beforeOrAfterPipeOperator.
    def visitBeforeOrAfterPipeOperator(self, ctx:KqlParser.BeforeOrAfterPipeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkPipeOperator.
    def visitForkPipeOperator(self, ctx:KqlParser.ForkPipeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#asOperator.
    def visitAsOperator(self, ctx:KqlParser.AsOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#assertSchemaOperator.
    def visitAssertSchemaOperator(self, ctx:KqlParser.AssertSchemaOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#consumeOperator.
    def visitConsumeOperator(self, ctx:KqlParser.ConsumeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#countOperator.
    def visitCountOperator(self, ctx:KqlParser.CountOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#countOperatorAsClause.
    def visitCountOperatorAsClause(self, ctx:KqlParser.CountOperatorAsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#distinctOperator.
    def visitDistinctOperator(self, ctx:KqlParser.DistinctOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#distinctOperatorStarTarget.
    def visitDistinctOperatorStarTarget(self, ctx:KqlParser.DistinctOperatorStarTargetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#distinctOperatorColumnListTarget.
    def visitDistinctOperatorColumnListTarget(self, ctx:KqlParser.DistinctOperatorColumnListTargetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#evaluateOperator.
    def visitEvaluateOperator(self, ctx:KqlParser.EvaluateOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#evaluateOperatorSchemaClause.
    def visitEvaluateOperatorSchemaClause(self, ctx:KqlParser.EvaluateOperatorSchemaClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#extendOperator.
    def visitExtendOperator(self, ctx:KqlParser.ExtendOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#executeAndCacheOperator.
    def visitExecuteAndCacheOperator(self, ctx:KqlParser.ExecuteAndCacheOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#facetByOperator.
    def visitFacetByOperator(self, ctx:KqlParser.FacetByOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#facetByOperatorWithOperatorClause.
    def visitFacetByOperatorWithOperatorClause(self, ctx:KqlParser.FacetByOperatorWithOperatorClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#facetByOperatorWithExpressionClause.
    def visitFacetByOperatorWithExpressionClause(self, ctx:KqlParser.FacetByOperatorWithExpressionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperator.
    def visitFindOperator(self, ctx:KqlParser.FindOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorParametersWhereClause.
    def visitFindOperatorParametersWhereClause(self, ctx:KqlParser.FindOperatorParametersWhereClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorInClause.
    def visitFindOperatorInClause(self, ctx:KqlParser.FindOperatorInClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectClause.
    def visitFindOperatorProjectClause(self, ctx:KqlParser.FindOperatorProjectClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectExpression.
    def visitFindOperatorProjectExpression(self, ctx:KqlParser.FindOperatorProjectExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorColumnExpression.
    def visitFindOperatorColumnExpression(self, ctx:KqlParser.FindOperatorColumnExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorOptionalColumnType.
    def visitFindOperatorOptionalColumnType(self, ctx:KqlParser.FindOperatorOptionalColumnTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorPackExpression.
    def visitFindOperatorPackExpression(self, ctx:KqlParser.FindOperatorPackExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectSmartClause.
    def visitFindOperatorProjectSmartClause(self, ctx:KqlParser.FindOperatorProjectSmartClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectAwayClause.
    def visitFindOperatorProjectAwayClause(self, ctx:KqlParser.FindOperatorProjectAwayClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectAwayStar.
    def visitFindOperatorProjectAwayStar(self, ctx:KqlParser.FindOperatorProjectAwayStarContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorProjectAwayColumnList.
    def visitFindOperatorProjectAwayColumnList(self, ctx:KqlParser.FindOperatorProjectAwayColumnListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorSource.
    def visitFindOperatorSource(self, ctx:KqlParser.FindOperatorSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#findOperatorSourceEntityExpression.
    def visitFindOperatorSourceEntityExpression(self, ctx:KqlParser.FindOperatorSourceEntityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkOperator.
    def visitForkOperator(self, ctx:KqlParser.ForkOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkOperatorFork.
    def visitForkOperatorFork(self, ctx:KqlParser.ForkOperatorForkContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkOperatorExpressionName.
    def visitForkOperatorExpressionName(self, ctx:KqlParser.ForkOperatorExpressionNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkOperatorExpression.
    def visitForkOperatorExpression(self, ctx:KqlParser.ForkOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#forkOperatorPipedOperator.
    def visitForkOperatorPipedOperator(self, ctx:KqlParser.ForkOperatorPipedOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#getSchemaOperator.
    def visitGetSchemaOperator(self, ctx:KqlParser.GetSchemaOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMarkComponentsOperator.
    def visitGraphMarkComponentsOperator(self, ctx:KqlParser.GraphMarkComponentsOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchOperator.
    def visitGraphMatchOperator(self, ctx:KqlParser.GraphMatchOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchPattern.
    def visitGraphMatchPattern(self, ctx:KqlParser.GraphMatchPatternContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchPatternNode.
    def visitGraphMatchPatternNode(self, ctx:KqlParser.GraphMatchPatternNodeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchPatternUnnamedEdge.
    def visitGraphMatchPatternUnnamedEdge(self, ctx:KqlParser.GraphMatchPatternUnnamedEdgeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchPatternNamedEdge.
    def visitGraphMatchPatternNamedEdge(self, ctx:KqlParser.GraphMatchPatternNamedEdgeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchPatternRange.
    def visitGraphMatchPatternRange(self, ctx:KqlParser.GraphMatchPatternRangeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchWhereClause.
    def visitGraphMatchWhereClause(self, ctx:KqlParser.GraphMatchWhereClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphMatchProjectClause.
    def visitGraphMatchProjectClause(self, ctx:KqlParser.GraphMatchProjectClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphToTableOperator.
    def visitGraphToTableOperator(self, ctx:KqlParser.GraphToTableOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphToTableOutput.
    def visitGraphToTableOutput(self, ctx:KqlParser.GraphToTableOutputContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphToTableAsClause.
    def visitGraphToTableAsClause(self, ctx:KqlParser.GraphToTableAsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#graphShortestPathsOperator.
    def visitGraphShortestPathsOperator(self, ctx:KqlParser.GraphShortestPathsOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#invokeOperator.
    def visitInvokeOperator(self, ctx:KqlParser.InvokeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#joinOperator.
    def visitJoinOperator(self, ctx:KqlParser.JoinOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#joinOperatorOnClause.
    def visitJoinOperatorOnClause(self, ctx:KqlParser.JoinOperatorOnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#joinOperatorWhereClause.
    def visitJoinOperatorWhereClause(self, ctx:KqlParser.JoinOperatorWhereClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#lookupOperator.
    def visitLookupOperator(self, ctx:KqlParser.LookupOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#macroExpandOperator.
    def visitMacroExpandOperator(self, ctx:KqlParser.MacroExpandOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#macroExpandEntityGroup.
    def visitMacroExpandEntityGroup(self, ctx:KqlParser.MacroExpandEntityGroupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityGroupExpression.
    def visitEntityGroupExpression(self, ctx:KqlParser.EntityGroupExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeGraphOperator.
    def visitMakeGraphOperator(self, ctx:KqlParser.MakeGraphOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeGraphIdClause.
    def visitMakeGraphIdClause(self, ctx:KqlParser.MakeGraphIdClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeGraphTablesAndKeysClause.
    def visitMakeGraphTablesAndKeysClause(self, ctx:KqlParser.MakeGraphTablesAndKeysClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeGraphPartitionedByClause.
    def visitMakeGraphPartitionedByClause(self, ctx:KqlParser.MakeGraphPartitionedByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperator.
    def visitMakeSeriesOperator(self, ctx:KqlParser.MakeSeriesOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorOnClause.
    def visitMakeSeriesOperatorOnClause(self, ctx:KqlParser.MakeSeriesOperatorOnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorAggregation.
    def visitMakeSeriesOperatorAggregation(self, ctx:KqlParser.MakeSeriesOperatorAggregationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorExpressionDefaultClause.
    def visitMakeSeriesOperatorExpressionDefaultClause(self, ctx:KqlParser.MakeSeriesOperatorExpressionDefaultClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorInRangeClause.
    def visitMakeSeriesOperatorInRangeClause(self, ctx:KqlParser.MakeSeriesOperatorInRangeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorFromToStepClause.
    def visitMakeSeriesOperatorFromToStepClause(self, ctx:KqlParser.MakeSeriesOperatorFromToStepClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#makeSeriesOperatorByClause.
    def visitMakeSeriesOperatorByClause(self, ctx:KqlParser.MakeSeriesOperatorByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvapplyOperator.
    def visitMvapplyOperator(self, ctx:KqlParser.MvapplyOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvapplyOperatorLimitClause.
    def visitMvapplyOperatorLimitClause(self, ctx:KqlParser.MvapplyOperatorLimitClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvapplyOperatorIdClause.
    def visitMvapplyOperatorIdClause(self, ctx:KqlParser.MvapplyOperatorIdClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvapplyOperatorExpression.
    def visitMvapplyOperatorExpression(self, ctx:KqlParser.MvapplyOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvapplyOperatorExpressionToClause.
    def visitMvapplyOperatorExpressionToClause(self, ctx:KqlParser.MvapplyOperatorExpressionToClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvexpandOperator.
    def visitMvexpandOperator(self, ctx:KqlParser.MvexpandOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#mvexpandOperatorExpression.
    def visitMvexpandOperatorExpression(self, ctx:KqlParser.MvexpandOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperator.
    def visitParseOperator(self, ctx:KqlParser.ParseOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperatorKindClause.
    def visitParseOperatorKindClause(self, ctx:KqlParser.ParseOperatorKindClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperatorFlagsClause.
    def visitParseOperatorFlagsClause(self, ctx:KqlParser.ParseOperatorFlagsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperatorNameAndOptionalType.
    def visitParseOperatorNameAndOptionalType(self, ctx:KqlParser.ParseOperatorNameAndOptionalTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperatorPattern.
    def visitParseOperatorPattern(self, ctx:KqlParser.ParseOperatorPatternContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseOperatorPatternSegment.
    def visitParseOperatorPatternSegment(self, ctx:KqlParser.ParseOperatorPatternSegmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseWhereOperator.
    def visitParseWhereOperator(self, ctx:KqlParser.ParseWhereOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseKvOperator.
    def visitParseKvOperator(self, ctx:KqlParser.ParseKvOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parseKvWithClause.
    def visitParseKvWithClause(self, ctx:KqlParser.ParseKvWithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionOperator.
    def visitPartitionOperator(self, ctx:KqlParser.PartitionOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionOperatorInClause.
    def visitPartitionOperatorInClause(self, ctx:KqlParser.PartitionOperatorInClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionOperatorSubExpressionBody.
    def visitPartitionOperatorSubExpressionBody(self, ctx:KqlParser.PartitionOperatorSubExpressionBodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionOperatorFullExpressionBody.
    def visitPartitionOperatorFullExpressionBody(self, ctx:KqlParser.PartitionOperatorFullExpressionBodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionByOperator.
    def visitPartitionByOperator(self, ctx:KqlParser.PartitionByOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#partitionByOperatorIdClause.
    def visitPartitionByOperatorIdClause(self, ctx:KqlParser.PartitionByOperatorIdClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#printOperator.
    def visitPrintOperator(self, ctx:KqlParser.PrintOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectAwayOperator.
    def visitProjectAwayOperator(self, ctx:KqlParser.ProjectAwayOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectKeepOperator.
    def visitProjectKeepOperator(self, ctx:KqlParser.ProjectKeepOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectOperator.
    def visitProjectOperator(self, ctx:KqlParser.ProjectOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectRenameOperator.
    def visitProjectRenameOperator(self, ctx:KqlParser.ProjectRenameOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectReorderOperator.
    def visitProjectReorderOperator(self, ctx:KqlParser.ProjectReorderOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#projectReorderExpression.
    def visitProjectReorderExpression(self, ctx:KqlParser.ProjectReorderExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#reduceByOperator.
    def visitReduceByOperator(self, ctx:KqlParser.ReduceByOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#reduceByWithClause.
    def visitReduceByWithClause(self, ctx:KqlParser.ReduceByWithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderOperator.
    def visitRenderOperator(self, ctx:KqlParser.RenderOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderOperatorWithClause.
    def visitRenderOperatorWithClause(self, ctx:KqlParser.RenderOperatorWithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderOperatorLegacyPropertyList.
    def visitRenderOperatorLegacyPropertyList(self, ctx:KqlParser.RenderOperatorLegacyPropertyListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderOperatorProperty.
    def visitRenderOperatorProperty(self, ctx:KqlParser.RenderOperatorPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderPropertyNameList.
    def visitRenderPropertyNameList(self, ctx:KqlParser.RenderPropertyNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#renderOperatorLegacyProperty.
    def visitRenderOperatorLegacyProperty(self, ctx:KqlParser.RenderOperatorLegacyPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#sampleDistinctOperator.
    def visitSampleDistinctOperator(self, ctx:KqlParser.SampleDistinctOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#sampleOperator.
    def visitSampleOperator(self, ctx:KqlParser.SampleOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperator.
    def visitScanOperator(self, ctx:KqlParser.ScanOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorOrderByClause.
    def visitScanOperatorOrderByClause(self, ctx:KqlParser.ScanOperatorOrderByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorPartitionByClause.
    def visitScanOperatorPartitionByClause(self, ctx:KqlParser.ScanOperatorPartitionByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorDeclareClause.
    def visitScanOperatorDeclareClause(self, ctx:KqlParser.ScanOperatorDeclareClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorStep.
    def visitScanOperatorStep(self, ctx:KqlParser.ScanOperatorStepContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorStepOutputClause.
    def visitScanOperatorStepOutputClause(self, ctx:KqlParser.ScanOperatorStepOutputClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorBody.
    def visitScanOperatorBody(self, ctx:KqlParser.ScanOperatorBodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scanOperatorAssignment.
    def visitScanOperatorAssignment(self, ctx:KqlParser.ScanOperatorAssignmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#searchOperator.
    def visitSearchOperator(self, ctx:KqlParser.SearchOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#searchOperatorStarAndExpression.
    def visitSearchOperatorStarAndExpression(self, ctx:KqlParser.SearchOperatorStarAndExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#searchOperatorInClause.
    def visitSearchOperatorInClause(self, ctx:KqlParser.SearchOperatorInClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#serializeOperator.
    def visitSerializeOperator(self, ctx:KqlParser.SerializeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#sortOperator.
    def visitSortOperator(self, ctx:KqlParser.SortOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#orderedExpression.
    def visitOrderedExpression(self, ctx:KqlParser.OrderedExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#sortOrdering.
    def visitSortOrdering(self, ctx:KqlParser.SortOrderingContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#summarizeOperator.
    def visitSummarizeOperator(self, ctx:KqlParser.SummarizeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#summarizeOperatorByClause.
    def visitSummarizeOperatorByClause(self, ctx:KqlParser.SummarizeOperatorByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#summarizeOperatorLegacyBinClause.
    def visitSummarizeOperatorLegacyBinClause(self, ctx:KqlParser.SummarizeOperatorLegacyBinClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#takeOperator.
    def visitTakeOperator(self, ctx:KqlParser.TakeOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topOperator.
    def visitTopOperator(self, ctx:KqlParser.TopOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topHittersOperator.
    def visitTopHittersOperator(self, ctx:KqlParser.TopHittersOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topHittersOperatorByClause.
    def visitTopHittersOperatorByClause(self, ctx:KqlParser.TopHittersOperatorByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topNestedOperator.
    def visitTopNestedOperator(self, ctx:KqlParser.TopNestedOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topNestedOperatorPart.
    def visitTopNestedOperatorPart(self, ctx:KqlParser.TopNestedOperatorPartContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#topNestedOperatorWithOthersClause.
    def visitTopNestedOperatorWithOthersClause(self, ctx:KqlParser.TopNestedOperatorWithOthersClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#unionOperator.
    def visitUnionOperator(self, ctx:KqlParser.UnionOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#unionOperatorExpression.
    def visitUnionOperatorExpression(self, ctx:KqlParser.UnionOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#whereOperator.
    def visitWhereOperator(self, ctx:KqlParser.WhereOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#contextualSubExpression.
    def visitContextualSubExpression(self, ctx:KqlParser.ContextualSubExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#contextualPipeExpression.
    def visitContextualPipeExpression(self, ctx:KqlParser.ContextualPipeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#contextualPipeExpressionPipedOperator.
    def visitContextualPipeExpressionPipedOperator(self, ctx:KqlParser.ContextualPipeExpressionPipedOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#strictQueryOperatorParameter.
    def visitStrictQueryOperatorParameter(self, ctx:KqlParser.StrictQueryOperatorParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#relaxedQueryOperatorParameter.
    def visitRelaxedQueryOperatorParameter(self, ctx:KqlParser.RelaxedQueryOperatorParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#queryOperatorProperty.
    def visitQueryOperatorProperty(self, ctx:KqlParser.QueryOperatorPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#namedExpression.
    def visitNamedExpression(self, ctx:KqlParser.NamedExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#namedExpressionNameClause.
    def visitNamedExpressionNameClause(self, ctx:KqlParser.NamedExpressionNameClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#namedExpressionNameList.
    def visitNamedExpressionNameList(self, ctx:KqlParser.NamedExpressionNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scopedFunctionCallExpression.
    def visitScopedFunctionCallExpression(self, ctx:KqlParser.ScopedFunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#unnamedExpression.
    def visitUnnamedExpression(self, ctx:KqlParser.UnnamedExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#logicalOrExpression.
    def visitLogicalOrExpression(self, ctx:KqlParser.LogicalOrExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#logicalOrOperation.
    def visitLogicalOrOperation(self, ctx:KqlParser.LogicalOrOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#logicalAndExpression.
    def visitLogicalAndExpression(self, ctx:KqlParser.LogicalAndExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#logicalAndOperation.
    def visitLogicalAndOperation(self, ctx:KqlParser.LogicalAndOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#equalityExpression.
    def visitEqualityExpression(self, ctx:KqlParser.EqualityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#equalsEqualityExpression.
    def visitEqualsEqualityExpression(self, ctx:KqlParser.EqualsEqualityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#listEqualityExpression.
    def visitListEqualityExpression(self, ctx:KqlParser.ListEqualityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#betweenEqualityExpression.
    def visitBetweenEqualityExpression(self, ctx:KqlParser.BetweenEqualityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#starEqualityExpression.
    def visitStarEqualityExpression(self, ctx:KqlParser.StarEqualityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#relationalExpression.
    def visitRelationalExpression(self, ctx:KqlParser.RelationalExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#additiveExpression.
    def visitAdditiveExpression(self, ctx:KqlParser.AdditiveExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#additiveOperation.
    def visitAdditiveOperation(self, ctx:KqlParser.AdditiveOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#multiplicativeExpression.
    def visitMultiplicativeExpression(self, ctx:KqlParser.MultiplicativeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#multiplicativeOperation.
    def visitMultiplicativeOperation(self, ctx:KqlParser.MultiplicativeOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringOperatorExpression.
    def visitStringOperatorExpression(self, ctx:KqlParser.StringOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringBinaryOperatorExpression.
    def visitStringBinaryOperatorExpression(self, ctx:KqlParser.StringBinaryOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringBinaryOperation.
    def visitStringBinaryOperation(self, ctx:KqlParser.StringBinaryOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringBinaryOperator.
    def visitStringBinaryOperator(self, ctx:KqlParser.StringBinaryOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringStarOperatorExpression.
    def visitStringStarOperatorExpression(self, ctx:KqlParser.StringStarOperatorExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#invocationExpression.
    def visitInvocationExpression(self, ctx:KqlParser.InvocationExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathExpression.
    def visitFunctionCallOrPathExpression(self, ctx:KqlParser.FunctionCallOrPathExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathRoot.
    def visitFunctionCallOrPathRoot(self, ctx:KqlParser.FunctionCallOrPathRootContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathPathExpression.
    def visitFunctionCallOrPathPathExpression(self, ctx:KqlParser.FunctionCallOrPathPathExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathOperation.
    def visitFunctionCallOrPathOperation(self, ctx:KqlParser.FunctionCallOrPathOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathPathOperation.
    def visitFunctionCallOrPathPathOperation(self, ctx:KqlParser.FunctionCallOrPathPathOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallOrPathElementOperation.
    def visitFunctionCallOrPathElementOperation(self, ctx:KqlParser.FunctionCallOrPathElementOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#legacyFunctionCallOrPathElementOperation.
    def visitLegacyFunctionCallOrPathElementOperation(self, ctx:KqlParser.LegacyFunctionCallOrPathElementOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#toScalarExpression.
    def visitToScalarExpression(self, ctx:KqlParser.ToScalarExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#toTableExpression.
    def visitToTableExpression(self, ctx:KqlParser.ToTableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#noOptimizationParameter.
    def visitNoOptimizationParameter(self, ctx:KqlParser.NoOptimizationParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dotCompositeFunctionCallExpression.
    def visitDotCompositeFunctionCallExpression(self, ctx:KqlParser.DotCompositeFunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dotCompositeFunctionCallOperation.
    def visitDotCompositeFunctionCallOperation(self, ctx:KqlParser.DotCompositeFunctionCallOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#functionCallExpression.
    def visitFunctionCallExpression(self, ctx:KqlParser.FunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#namedFunctionCallExpression.
    def visitNamedFunctionCallExpression(self, ctx:KqlParser.NamedFunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#argumentExpression.
    def visitArgumentExpression(self, ctx:KqlParser.ArgumentExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#countExpression.
    def visitCountExpression(self, ctx:KqlParser.CountExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#starExpression.
    def visitStarExpression(self, ctx:KqlParser.StarExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#primaryExpression.
    def visitPrimaryExpression(self, ctx:KqlParser.PrimaryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#nameReferenceWithDataScope.
    def visitNameReferenceWithDataScope(self, ctx:KqlParser.NameReferenceWithDataScopeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dataScopeClause.
    def visitDataScopeClause(self, ctx:KqlParser.DataScopeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parenthesizedExpression.
    def visitParenthesizedExpression(self, ctx:KqlParser.ParenthesizedExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#rangeExpression.
    def visitRangeExpression(self, ctx:KqlParser.RangeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityExpression.
    def visitEntityExpression(self, ctx:KqlParser.EntityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityPathOrElementExpression.
    def visitEntityPathOrElementExpression(self, ctx:KqlParser.EntityPathOrElementExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityPathOrElementOperator.
    def visitEntityPathOrElementOperator(self, ctx:KqlParser.EntityPathOrElementOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityPathOperator.
    def visitEntityPathOperator(self, ctx:KqlParser.EntityPathOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityElementOperator.
    def visitEntityElementOperator(self, ctx:KqlParser.EntityElementOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#legacyEntityPathElementOperator.
    def visitLegacyEntityPathElementOperator(self, ctx:KqlParser.LegacyEntityPathElementOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityName.
    def visitEntityName(self, ctx:KqlParser.EntityNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#entityNameReference.
    def visitEntityNameReference(self, ctx:KqlParser.EntityNameReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#atSignName.
    def visitAtSignName(self, ctx:KqlParser.AtSignNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#extendedPathName.
    def visitExtendedPathName(self, ctx:KqlParser.ExtendedPathNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedEntityExpression.
    def visitWildcardedEntityExpression(self, ctx:KqlParser.WildcardedEntityExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedPathExpression.
    def visitWildcardedPathExpression(self, ctx:KqlParser.WildcardedPathExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedPathName.
    def visitWildcardedPathName(self, ctx:KqlParser.WildcardedPathNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#contextualDataTableExpression.
    def visitContextualDataTableExpression(self, ctx:KqlParser.ContextualDataTableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dataTableExpression.
    def visitDataTableExpression(self, ctx:KqlParser.DataTableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#rowSchema.
    def visitRowSchema(self, ctx:KqlParser.RowSchemaContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#rowSchemaColumnDeclaration.
    def visitRowSchemaColumnDeclaration(self, ctx:KqlParser.RowSchemaColumnDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#externalDataExpression.
    def visitExternalDataExpression(self, ctx:KqlParser.ExternalDataExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#externalDataWithClause.
    def visitExternalDataWithClause(self, ctx:KqlParser.ExternalDataWithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#externalDataWithClauseProperty.
    def visitExternalDataWithClauseProperty(self, ctx:KqlParser.ExternalDataWithClausePropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#materializedViewCombineExpression.
    def visitMaterializedViewCombineExpression(self, ctx:KqlParser.MaterializedViewCombineExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#materializeViewCombineBaseClause.
    def visitMaterializeViewCombineBaseClause(self, ctx:KqlParser.MaterializeViewCombineBaseClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#materializedViewCombineDeltaClause.
    def visitMaterializedViewCombineDeltaClause(self, ctx:KqlParser.MaterializedViewCombineDeltaClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#materializedViewCombineAggregationsClause.
    def visitMaterializedViewCombineAggregationsClause(self, ctx:KqlParser.MaterializedViewCombineAggregationsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#scalarType.
    def visitScalarType(self, ctx:KqlParser.ScalarTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#extendedScalarType.
    def visitExtendedScalarType(self, ctx:KqlParser.ExtendedScalarTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#parameterName.
    def visitParameterName(self, ctx:KqlParser.ParameterNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#simpleNameReference.
    def visitSimpleNameReference(self, ctx:KqlParser.SimpleNameReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#extendedNameReference.
    def visitExtendedNameReference(self, ctx:KqlParser.ExtendedNameReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedNameReference.
    def visitWildcardedNameReference(self, ctx:KqlParser.WildcardedNameReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#simpleOrWildcardedNameReference.
    def visitSimpleOrWildcardedNameReference(self, ctx:KqlParser.SimpleOrWildcardedNameReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#identifierName.
    def visitIdentifierName(self, ctx:KqlParser.IdentifierNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#keywordName.
    def visitKeywordName(self, ctx:KqlParser.KeywordNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#extendedKeywordName.
    def visitExtendedKeywordName(self, ctx:KqlParser.ExtendedKeywordNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#escapedName.
    def visitEscapedName(self, ctx:KqlParser.EscapedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#identifierOrKeywordName.
    def visitIdentifierOrKeywordName(self, ctx:KqlParser.IdentifierOrKeywordNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#identifierOrKeywordOrEscapedName.
    def visitIdentifierOrKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrKeywordOrEscapedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#identifierOrExtendedKeywordOrEscapedName.
    def visitIdentifierOrExtendedKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrExtendedKeywordOrEscapedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#identifierOrExtendedKeywordName.
    def visitIdentifierOrExtendedKeywordName(self, ctx:KqlParser.IdentifierOrExtendedKeywordNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedName.
    def visitWildcardedName(self, ctx:KqlParser.WildcardedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedNamePrefix.
    def visitWildcardedNamePrefix(self, ctx:KqlParser.WildcardedNamePrefixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#wildcardedNameSegment.
    def visitWildcardedNameSegment(self, ctx:KqlParser.WildcardedNameSegmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#literalExpression.
    def visitLiteralExpression(self, ctx:KqlParser.LiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#unsignedLiteralExpression.
    def visitUnsignedLiteralExpression(self, ctx:KqlParser.UnsignedLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#numberLikeLiteralExpression.
    def visitNumberLikeLiteralExpression(self, ctx:KqlParser.NumberLikeLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#numericLiteralExpression.
    def visitNumericLiteralExpression(self, ctx:KqlParser.NumericLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#signedLiteralExpression.
    def visitSignedLiteralExpression(self, ctx:KqlParser.SignedLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#longLiteralExpression.
    def visitLongLiteralExpression(self, ctx:KqlParser.LongLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#intLiteralExpression.
    def visitIntLiteralExpression(self, ctx:KqlParser.IntLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#realLiteralExpression.
    def visitRealLiteralExpression(self, ctx:KqlParser.RealLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#decimalLiteralExpression.
    def visitDecimalLiteralExpression(self, ctx:KqlParser.DecimalLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dateTimeLiteralExpression.
    def visitDateTimeLiteralExpression(self, ctx:KqlParser.DateTimeLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#timeSpanLiteralExpression.
    def visitTimeSpanLiteralExpression(self, ctx:KqlParser.TimeSpanLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#booleanLiteralExpression.
    def visitBooleanLiteralExpression(self, ctx:KqlParser.BooleanLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#guidLiteralExpression.
    def visitGuidLiteralExpression(self, ctx:KqlParser.GuidLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#typeLiteralExpression.
    def visitTypeLiteralExpression(self, ctx:KqlParser.TypeLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#signedLongLiteralExpression.
    def visitSignedLongLiteralExpression(self, ctx:KqlParser.SignedLongLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#signedRealLiteralExpression.
    def visitSignedRealLiteralExpression(self, ctx:KqlParser.SignedRealLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#stringLiteralExpression.
    def visitStringLiteralExpression(self, ctx:KqlParser.StringLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#dynamicLiteralExpression.
    def visitDynamicLiteralExpression(self, ctx:KqlParser.DynamicLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonValue.
    def visitJsonValue(self, ctx:KqlParser.JsonValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonObject.
    def visitJsonObject(self, ctx:KqlParser.JsonObjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonPair.
    def visitJsonPair(self, ctx:KqlParser.JsonPairContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonArray.
    def visitJsonArray(self, ctx:KqlParser.JsonArrayContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonBoolean.
    def visitJsonBoolean(self, ctx:KqlParser.JsonBooleanContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonDateTime.
    def visitJsonDateTime(self, ctx:KqlParser.JsonDateTimeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonGuid.
    def visitJsonGuid(self, ctx:KqlParser.JsonGuidContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonNull.
    def visitJsonNull(self, ctx:KqlParser.JsonNullContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonString.
    def visitJsonString(self, ctx:KqlParser.JsonStringContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonTimeSpan.
    def visitJsonTimeSpan(self, ctx:KqlParser.JsonTimeSpanContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonLong.
    def visitJsonLong(self, ctx:KqlParser.JsonLongContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KqlParser#jsonReal.
    def visitJsonReal(self, ctx:KqlParser.JsonRealContext):
        return self.visitChildren(ctx)



del KqlParser